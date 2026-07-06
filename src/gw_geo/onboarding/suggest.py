"""Domain-first onboarding auto-fill (M5): from a bare domain, propose the brand's **name** and a
list of likely **competitors** in one LLM call -- both pre-filled and fully editable by the user in
the onboarding wizard.

Two injected seams, so the whole module is hermetic and makes **no** network/LLM call under test:

- ``fetcher: PageFetcher`` -- the ranking crawler's fetch seam (:mod:`gw_geo.ranking.fetch`). The
  production wiring injects the SSRF-guarded :class:`~gw_geo.ranking.fetch.HttpxPageFetcher`, so this
  module never opens its own socket and inherits that guard; tests inject a dict/markup-backed fake.
  Its only role here is to surface a *name hint* (a parsed ``<title>``/``og:site_name``/JSON-LD name,
  or a bounded snippet of the page's visible text) that is fed into the LLM prompt.
- ``llm: LLMClient`` -- the content engine's generation seam (:mod:`gw_geo.content.generate`), reused
  verbatim so this call goes through the same flag-selected backend the content pipeline uses
  (local Claude by default, else Portkey/direct); tests inject a canned fake.

The LLM returns ``{name, competitors}``: it derives the brand name from the **domain** (refined by
the page hint when available), which is more accurate than a visible-text parse -- in prod the
fetched page is stripped to visible text, so the ``<title>``/JSON-LD parsers don't fire and a
markup-only name resolver would always fall to the crude domain heuristic. That heuristic
(:func:`_name_from_domain`) is used **only** when the LLM's name comes back empty.

Both suggestions are *best-effort and non-fatal*: a fetch failure just drops the hint, and any LLM
failure/empty/malformed response yields the domain-heuristic name and ``[]`` competitors --
onboarding must always proceed to manual entry, never raise (PRD: white-hat, no fabricated claims --
these are grounded suggestions the user edits, not asserted facts).
"""

from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup
from bs4.element import Tag
from pydantic import BaseModel, Field

from gw_geo.content.generate import LLMClient
from gw_geo.ranking.fetch import PageFetcher

# Up to ~6 competitor suggestions (ui-spec onboarding: a short, editable seed list, not a directory).
_MAX_COMPETITORS = 6

# Cap on the page-derived name hint fed into the prompt -- a bounded snippet, never the whole page.
_HINT_MAX_CHARS = 400

# schema.org `@type`s we read a brand `name` off, in priority order (Organization before WebSite).
_ORG_TYPES = frozenset({"Organization"})
_SITE_TYPES = frozenset({"WebSite", "Website"})

# A `<title>` separator surrounded by whitespace ("Acme | tagline", "Acme - Home", "Acme – X"): the
# text before it is the brand, the rest is boilerplate. Whitespace-anchored so a hyphenated brand
# ("Acme-Corp", no surrounding spaces) is never split.
_TITLE_SEP_RE = re.compile(r"\s+[|–—·»:\-]\s+")

_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


# --- structured-output contract for the brand-suggestion tool call ---------------------------

# Free-form-friendly object schema (same forced-tool-call pattern `content.generate` uses, which
# Portkey maps to lenient provider tool-use -- see `PortkeyLLMClient`): the brand `name` plus a
# list of competitor {name, domain?}. The same schema rides the local-Claude path via `--json-schema`.
_BRAND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "The brand/company's proper name (as it writes it), derived from the "
            "domain and refined by the page hint when one is given.",
        },
        "competitors": {
            "type": "array",
            "description": "Real, well-known companies that compete with the brand in its market.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The competitor company's name."},
                    "domain": {
                        "type": "string",
                        "description": "Its primary website domain, if known (else omit).",
                    },
                },
                "required": ["name"],
            },
        },
    },
    "required": ["name", "competitors"],
}

_BRAND_SYSTEM = (
    "You identify the company/brand behind a website domain and its real, well-known competitors. "
    "Derive the brand's proper name from the domain (using any page hint only to refine it), and "
    "return only plausible, genuinely-existing competitors in the same market -- never invent or "
    "fabricate a company. When unsure, return fewer rather than guessing. Respond only via the "
    "requested tool call."
)


class BrandSuggestion(BaseModel):
    """The onboarding auto-fill: a proposed brand ``name`` (from the site), the echoed ``domain``,
    and up to ~6 suggested ``competitors`` (names). Every field is a *suggestion* the user edits in
    the wizard -- nothing here is persisted until the user submits ``POST /brands``.
    """

    name: str
    domain: str
    competitors: list[str] = Field(default_factory=list)


def normalize_url(domain: str) -> str:
    """Turn a user-typed domain into a fetchable URL: trim, and prepend ``https://`` if schemeless.

    ``"acme.com" -> "https://acme.com"``; an already-schemed value (``http(s)://...``) is left as-is.
    The result is handed to the SSRF-guarded fetcher, which vets the host.
    """
    text = domain.strip()
    if text and not _SCHEME_RE.match(text):
        text = f"https://{text}"
    return text


# --- brand-name extraction (priority: JSON-LD -> og:site_name -> <title> -> domain heuristic) ----


def _type_matches(node: dict[str, Any], wanted: frozenset[str]) -> bool:
    """True if a JSON-LD node's ``@type`` (a string or a list of strings) intersects ``wanted``."""
    value = node.get("@type")
    if isinstance(value, str):
        return value in wanted
    if isinstance(value, list):
        return any(isinstance(item, str) and item in wanted for item in value)
    return False


def _find_named(obj: Any, wanted: frozenset[str]) -> str | None:
    """Depth-first search a parsed JSON-LD object for the first ``@type in wanted`` node's ``name``.

    Mirrors :func:`gw_geo.ranking.fetch._find_date_published`: JSON-LD is often a bare object, a list
    of them, or wraps the real entity in an ``@graph`` array, so recurse through dicts *and* lists.
    """
    if isinstance(obj, dict):
        if _type_matches(obj, wanted):
            name = obj.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        for nested in obj.values():
            found = _find_named(nested, wanted)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_named(item, wanted)
            if found is not None:
                return found
    return None


def _name_from_jsonld(soup: BeautifulSoup) -> str | None:
    """The brand ``name`` from any ``application/ld+json`` block -- Organization first, then WebSite."""
    blocks: list[Any] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if not isinstance(script, Tag):
            continue
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            blocks.append(json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            continue
    for wanted in (_ORG_TYPES, _SITE_TYPES):
        for data in blocks:
            found = _find_named(data, wanted)
            if found is not None:
                return found
    return None


def _name_from_og_site_name(soup: BeautifulSoup) -> str | None:
    """The brand name from ``<meta property="og:site_name" content="...">``, else ``None``."""
    for meta in soup.find_all("meta", attrs={"property": "og:site_name"}):
        if not isinstance(meta, Tag):
            continue
        content = meta.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return None


def _name_from_title(soup: BeautifulSoup) -> str | None:
    """The brand name from ``<title>``, with any trailing ``" | tagline"`` / ``" - …"`` stripped."""
    title = soup.find("title")
    if not isinstance(title, Tag):
        return None
    text = title.get_text(strip=True)
    if not text:
        return None
    head = _TITLE_SEP_RE.split(text, maxsplit=1)[0].strip()
    return head or None


def _name_from_markup(markup: str | None) -> str | None:
    """Best-effort brand name from page markup: JSON-LD -> og:site_name -> <title>; else ``None``."""
    if not markup:
        return None
    soup = BeautifulSoup(markup, "html.parser")
    return _name_from_jsonld(soup) or _name_from_og_site_name(soup) or _name_from_title(soup)


def _name_from_domain(domain: str) -> str:
    """Domain-derived fallback name: strip scheme/``www``/TLD, split on ``.``/``-``, title-case.

    ``"acme.com" -> "Acme"``, ``"https://www.foo-bar.io/" -> "Foo Bar"``. Never empty as long as the
    domain has any host characters (worst case: the cleaned host itself).
    """
    host = domain.strip().lower()
    host = _SCHEME_RE.sub("", host)
    host = host.split("/", 1)[0]  # drop any path
    host = host.split("@")[-1]  # drop userinfo
    host = host.split(":", 1)[0]  # drop port
    if host.startswith("www."):
        host = host[len("www.") :]
    labels = host.split(".")
    stem = ".".join(labels[:-1]) if len(labels) > 1 else host  # strip the TLD label
    words = [word for word in re.split(r"[.\-]+", stem) if word]
    return " ".join(word.capitalize() for word in words) if words else host


def _text_snippet(text: str) -> str | None:
    """A whitespace-collapsed, length-capped snippet of the page's visible text (or ``None``)."""
    snippet = " ".join(text.split())[:_HINT_MAX_CHARS]
    return snippet or None


def _fetch_name_hint(*, domain: str, fetcher: PageFetcher) -> str | None:
    """Fetch the site (never raising) and derive a brand-name *hint* for the LLM prompt.

    Prefers a parsed ``<title>``/``og:site_name``/JSON-LD name (fires when the fetcher surfaces raw
    markup, as fakes do); on the real, visible-text-only fetch that yields nothing, so it falls to a
    bounded snippet of the visible text. ``None`` when the page can't be fetched or is empty -- the
    LLM then derives the name from the domain alone.
    """
    try:
        page = fetcher.fetch(normalize_url(domain))
    except Exception:
        return None  # a broken/blocked/timed-out fetch must never break onboarding
    if page is None or not page.text:
        return None
    return _name_from_markup(page.text) or _text_snippet(page.text)


# --- competitor suggestion (one structured LLM tool call; empty on any failure) --------------


def _build_brand_prompt(*, domain: str, name_hint: str | None) -> str:
    lines = [
        f"Website domain: {domain}.",
        "Identify the company/brand that operates this website, and up to "
        f"{_MAX_COMPETITORS} real companies that directly compete with it in its market.",
    ]
    if name_hint:
        lines.append(
            "Hint read off the site's page (may be noisy boilerplate -- use only if it helps you "
            f"name the brand): {name_hint!r}."
        )
    lines.append(
        "Return the brand's proper name (as the company writes it -- derive it from the domain, "
        "refined by the hint if useful), and for each competitor its name and, if you know it, its "
        "primary website domain. Only include real, plausible competitors; omit any you are unsure "
        "about."
    )
    return "\n".join(lines)


def _parse_name(result: Any) -> str | None:
    """The trimmed brand ``name`` from the LLM tool-call result, or ``None`` if absent/blank/malformed.

    The caller falls back to the domain heuristic on ``None`` -- so a missing name never breaks
    onboarding.
    """
    if not isinstance(result, dict):
        return None
    name = result.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _parse_competitors(result: Any, *, brand_name: str) -> list[str]:
    """Map the LLM tool-call result to a clean ``list[str]`` of competitor names.

    Accepts either ``{"name": ..., "domain": ...}`` items or bare strings; drops blanks, the brand
    itself, and case-insensitive duplicates; caps at :data:`_MAX_COMPETITORS`. Any shape it doesn't
    understand yields ``[]`` (the caller treats that identically to an LLM failure).
    """
    if not isinstance(result, dict):
        return []
    raw = result.get("competitors")
    if not isinstance(raw, list):
        return []

    self_key = brand_name.strip().lower()
    seen: set[str] = set()
    names: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            candidate = item.get("name")
        elif isinstance(item, str):
            candidate = item
        else:
            candidate = None
        if not isinstance(candidate, str):
            continue
        candidate = candidate.strip()
        key = candidate.lower()
        if not candidate or key == self_key or key in seen:
            continue
        seen.add(key)
        names.append(candidate)
        if len(names) >= _MAX_COMPETITORS:
            break
    return names


def _call_brand_llm(
    *, domain: str, name_hint: str | None, llm: LLMClient
) -> dict[str, Any] | None:
    """One structured tool-call for ``{name, competitors}``; ``None`` on any failure (never raises)."""
    try:
        return llm.complete(
            system=_BRAND_SYSTEM,
            prompt=_build_brand_prompt(domain=domain, name_hint=name_hint),
            schema=_BRAND_SCHEMA,
        )
    except Exception:
        return None  # a failed/rate-limited/unconfigured LLM must never break onboarding


def suggest_brand_details(
    *, domain: str, fetcher: PageFetcher, llm: LLMClient
) -> BrandSuggestion:
    """Propose a brand ``name`` + ``competitors`` for a bare ``domain`` via one LLM tool-call.

    The LLM derives the name from the domain (refined by a page-title/text hint when the fetch
    surfaces one) and lists competitors. Best-effort and total: a fetch failure just drops the hint,
    and any LLM failure/empty name degrades to the domain heuristic with no competitors -- so the
    caller (``POST /brands/suggest``) always returns a usable, fully-editable suggestion and never
    surfaces a 5xx during onboarding.
    """
    clean_domain = domain.strip()
    name_hint = _fetch_name_hint(domain=clean_domain, fetcher=fetcher)
    result = _call_brand_llm(domain=clean_domain, name_hint=name_hint, llm=llm)
    name = _parse_name(result) or _name_from_domain(clean_domain)
    competitors = _parse_competitors(result, brand_name=name)
    return BrandSuggestion(name=name, domain=clean_domain, competitors=competitors)


__all__ = ["BrandSuggestion", "normalize_url", "suggest_brand_details"]
