"""Domain-first onboarding auto-fill (M5): from a bare domain, propose the brand's **name** (read
off its own site) and a list of likely **competitors** (via the LLM gateway) -- both pre-filled and
fully editable by the user in the onboarding wizard.

Two injected seams, so the whole module is hermetic and makes **no** network/LLM call under test:

- ``fetcher: PageFetcher`` -- the ranking crawler's fetch seam (:mod:`gw_geo.ranking.fetch`). The
  production wiring injects the SSRF-guarded :class:`~gw_geo.ranking.fetch.HttpxPageFetcher`, so this
  module never opens its own socket and inherits that guard; tests inject a dict/markup-backed fake.
- ``llm: LLMClient`` -- the content engine's generation seam (:mod:`gw_geo.content.generate`), reused
  verbatim so competitor suggestion goes through the same Portkey-routed, forced-tool-call structured
  output the content pipeline uses; tests inject a canned fake.

Both suggestions are *best-effort and non-fatal*: a fetch failure degrades the name to a
domain-derived heuristic, and any LLM failure/empty/malformed response yields ``[]`` competitors --
onboarding must always proceed to manual entry, never raise (PRD: white-hat, no fabricated claims --
competitors are grounded suggestions the user edits, not asserted facts).

Note on the name source: :class:`~gw_geo.ranking.fetch.FetchedPage` carries the page's *visible text*
(``<head>``/``<script>`` are stripped by the crawler), so the JSON-LD / ``og:site_name`` / ``<title>``
parsers below fire when the injected fetcher surfaces raw markup (as fakes do) and gracefully fall
through to the domain heuristic on the real, visible-text-only fetch -- see ``_resolve_brand_name``.
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

# schema.org `@type`s we read a brand `name` off, in priority order (Organization before WebSite).
_ORG_TYPES = frozenset({"Organization"})
_SITE_TYPES = frozenset({"WebSite", "Website"})

# A `<title>` separator surrounded by whitespace ("Acme | tagline", "Acme - Home", "Acme – X"): the
# text before it is the brand, the rest is boilerplate. Whitespace-anchored so a hyphenated brand
# ("Acme-Corp", no surrounding spaces) is never split.
_TITLE_SEP_RE = re.compile(r"\s+[|–—·»:\-]\s+")

_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


# --- structured-output contract for the competitor-suggestion tool call ----------------------

# Free-form-friendly object schema (same forced-tool-call pattern `content.generate` uses, which
# Portkey maps to lenient provider tool-use -- see `PortkeyLLMClient`): a list of {name, domain?}.
_COMPETITOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
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
        }
    },
    "required": ["competitors"],
}

_COMPETITOR_SYSTEM = (
    "You identify real, well-known competitor companies for a given brand. Return only plausible, "
    "genuinely-existing competitors in the same market -- never invent or fabricate a company. "
    "When unsure, return fewer rather than guessing. Respond only via the requested tool call."
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


def _resolve_brand_name(*, domain: str, fetcher: PageFetcher) -> str:
    """Fetch the site (never raising) and parse its brand name; fall back to the domain heuristic."""
    page = None
    try:
        page = fetcher.fetch(normalize_url(domain))
    except Exception:
        page = None  # a broken/blocked/timed-out fetch must never break onboarding
    markup = page.text if page is not None else None
    return _name_from_markup(markup) or _name_from_domain(domain)


# --- competitor suggestion (one structured LLM tool call; empty on any failure) --------------


def _build_competitor_prompt(*, name: str, domain: str) -> str:
    return (
        f"Brand: {name} (website: {domain}).\n"
        f"List up to {_MAX_COMPETITORS} real companies that directly compete with this brand in its "
        "market. For each, give the company name and, if you know it, its primary website domain. "
        "Only include real, plausible competitors; omit any you are unsure about."
    )


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


def _suggest_competitors(*, name: str, domain: str, llm: LLMClient) -> list[str]:
    """One structured tool-call to the LLM for competitor names; ``[]`` on any failure/empty."""
    try:
        result = llm.complete(
            system=_COMPETITOR_SYSTEM,
            prompt=_build_competitor_prompt(name=name, domain=domain),
            schema=_COMPETITOR_SCHEMA,
        )
    except Exception:
        return []  # a failed/rate-limited/unconfigured LLM must never break onboarding
    return _parse_competitors(result, brand_name=name)


def suggest_brand_details(
    *, domain: str, fetcher: PageFetcher, llm: LLMClient
) -> BrandSuggestion:
    """Propose a brand ``name`` (from its site) + ``competitors`` (via the LLM) for a bare ``domain``.

    Best-effort and total: a fetch failure degrades the name to the domain heuristic, and any LLM
    failure yields no competitors -- so the caller (``POST /brands/suggest``) always returns a usable,
    fully-editable suggestion and never surfaces a 5xx during onboarding.
    """
    clean_domain = domain.strip()
    name = _resolve_brand_name(domain=clean_domain, fetcher=fetcher)
    competitors = _suggest_competitors(name=name, domain=clean_domain, llm=llm)
    return BrandSuggestion(name=name, domain=clean_domain, competitors=competitors)


__all__ = ["BrandSuggestion", "normalize_url", "suggest_brand_details"]
