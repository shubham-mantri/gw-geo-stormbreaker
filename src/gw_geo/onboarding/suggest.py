"""Domain-first onboarding auto-fill (M5): from a bare domain, propose the brand's **name** and a
grounded, self-critiqued list of likely **competitors** -- both pre-filled and fully editable by the
user in the onboarding wizard.

A real test (respectmanufacturing.com) exposed the failure modes of a single ungrounded LLM call:
duplicate acquired entities (e.g. Nutricap Labs == NutraScience Labs, acquired 2015), scale/segment
mismatches (enterprise-scale contract manufacturers listed against an 11-50-employee SMB shop), and
missing product categories (only the dominant line, ignoring the target's cosmetics/skincare/OTC
lines). This module answers with a **three-stage pipeline** instead:

  (a) **Target profile** -- from the fetched page text (and, when the client can search the web, a
      brief current lookup) derive a compact profile: the brand name, the FULL set of product
      categories the target operates in, and size/segment hints (employee band, HQ, SMB vs
      enterprise). This is the grounding for the next two stages.
  (b) **Draft (web-search grounded)** -- ask for real, current competitors that match the target's
      size tier + customer segment, cover *every* category in the profile, are real companies
      (no fabrication), and carry a reason + size/segment tag + source. The prompt instructs the
      model to dedupe by ultimate parent (recognizing acquisitions/renames) and to bucket clearly
      larger-scale players as ``aspirational`` rather than direct.
  (c) **Critique -> refine** -- a second (fast, web-search-free) pass that reviews the draft against
      the profile: removes duplicates / renamed-or-acquired entities (keeping the survivor), drops
      or demotes scale/segment mismatches, and ensures every product category is represented
      (adding a real competitor for any uncovered one). Returns the final ordered, deduped names.

Two injected seams keep the module hermetic (no network/LLM call under test):

- ``fetcher: PageFetcher`` -- the ranking crawler's SSRF-guarded fetch seam
  (:mod:`gw_geo.ranking.fetch`); tests inject a dict/markup-backed fake. It surfaces the page's
  visible text + a name hint that ground stage (a).
- ``llm: LLMClient`` / ``critic: LLMClient`` -- the content engine's generation seam
  (:mod:`gw_geo.content.generate`), flag-selected in :mod:`gw_geo.content.gateway`. ``llm`` runs the
  research/draft stages (web-search-enabled on the local-Claude gateway); ``critic`` runs the
  refine stage (plain, no web search). Tests inject canned fakes; when ``critic`` is omitted the
  refine pass reuses ``llm``.

Every stage degrades gracefully and **never raises**: a failed fetch just drops the grounding, any
failed/empty/malformed LLM reply falls back to the best available result (ultimately the domain
heuristic name + an empty competitor list, i.e. onboarding proceeds to manual entry). White-hat,
PRD NG1: these are grounded suggestions the user edits, never fabricated asserted facts.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup
from bs4.element import Tag
from pydantic import BaseModel, Field

from gw_geo.content.generate import LLMClient
from gw_geo.ranking.fetch import PageFetcher

logger = logging.getLogger(__name__)

# A progress callback: ``(stage_key, human_label)``, called at the START of each pipeline stage.
# The keys are a stable contract (the async job store + the dashboard's progress UI key off them);
# the labels are the human-readable copy those surfaces render.
ProgressHook = Callable[[str, str], None]

# The ordered pipeline stages surfaced via ``on_progress`` (and logged so the backend terminal shows
# progress). ``"done"`` fires once the suggestion is ready. Threaded through the pipeline so the
# ~1-2 min grounded run reports where it is instead of a silent long-poll.
_STAGE_LABELS: dict[str, str] = {
    "fetching": "Fetching your site",
    "profiling": "Analyzing your brand and product categories",
    "researching": "Researching competitors across the web",
    "refining": "De-duplicating and checking category coverage",
    "done": "Done",
}

# Final competitor cap (ui-spec onboarding: a short, editable seed list, not a directory). The
# critique is asked to cap at this too; the deterministic clean-up below enforces it as a backstop.
_MAX_COMPETITORS = 8

# How many raw candidates the draft may propose before the critique trims them to the final set.
_DRAFT_CANDIDATES = 12

# Cap on the page-derived name hint fed into the prompt -- a bounded snippet, never the whole page.
_HINT_MAX_CHARS = 400

# Larger cap for the visible-text excerpt that grounds the profile stage (categories + size cues
# live deeper in the page than the title), still bounded so the prompt stays small.
_PAGE_MAX_CHARS = 2000

# schema.org `@type`s we read a brand `name` off, in priority order (Organization before WebSite).
_ORG_TYPES = frozenset({"Organization"})
_SITE_TYPES = frozenset({"WebSite", "Website"})

# A `<title>` separator surrounded by whitespace ("Acme | tagline", "Acme - Home", "Acme – X"): the
# text before it is the brand, the rest is boilerplate. Whitespace-anchored so a hyphenated brand
# ("Acme-Corp", no surrounding spaces) is never split.
_TITLE_SEP_RE = re.compile(r"\s+[|–—·»:\-]\s+")

_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


# --- structured-output contracts for each pipeline stage --------------------------------------
#
# Free-form-friendly object schemas (same forced-tool-call pattern `content.generate` uses, which
# Portkey maps to lenient provider tool-use, and the local-Claude path rides via `--json-schema`).
# The three are distinct objects so a fake `LLMClient` can dispatch on `schema is _X_SCHEMA`.

# (a) Target profile: name + the FULL category set + a size/segment hint.
_PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "The company/brand's proper name."},
        "categories": {
            "type": "array",
            "description": "EVERY distinct product/service category the company operates in "
            "(e.g. supplements AND cosmetics AND skincare AND OTC topicals -- not just the "
            "dominant line).",
            "items": {"type": "string"},
        },
        "size_segment": {
            "type": "string",
            "description": "Size & customer-segment hints: employee band, HQ, and whether it "
            "serves SMB vs mid-market vs enterprise customers.",
        },
    },
    "required": ["name", "categories"],
}

# (b) Draft: rich competitor candidates carrying the metadata the critique reasons over.
_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "competitors": {
            "type": "array",
            "description": "Real, currently-operating competitors -- never invent a company.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The competitor company's name."},
                    "reason": {"type": "string", "description": "One line: why it competes."},
                    "segment": {
                        "type": "string",
                        "description": "Its size/segment tag (e.g. 'SMB contract manufacturer').",
                    },
                    "category": {
                        "type": "string",
                        "description": "Which of the target's product categories it covers.",
                    },
                    "source": {
                        "type": "string",
                        "description": "A source URL backing the claim, if known (else omit).",
                    },
                    "tier": {
                        "type": "string",
                        "enum": ["direct", "aspirational"],
                        "description": "'direct' matches the target's size+segment; 'aspirational' "
                        "is a clearly larger / up-market player.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    "required": ["competitors"],
}

# (c) Critique: the final, ordered, deduped competitor NAMES (+ optional coverage note).
_CRITIQUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "competitors": {
            "type": "array",
            "description": "Final ordered competitor company names, strongest direct match first.",
            "items": {"type": "string"},
        },
        "coverage_notes": {
            "type": "string",
            "description": "Optional note on product-category coverage / any category left "
            "unrepresented.",
        },
    },
    "required": ["competitors"],
}

_PROFILE_SYSTEM = (
    "You research and profile the company behind a website so its competitive set can be scoped "
    "accurately. Using the page text provided -- and, if you can search the web, a brief current "
    "lookup -- determine the company's proper name; the FULL set of distinct product categories it "
    "operates in (list every one, e.g. supplements AND cosmetics AND skincare AND OTC topicals, "
    "not just its dominant line); and its size/segment (employee band, HQ if known, and whether it "
    "serves SMB, mid-market, or enterprise customers). Report only what the page and current, "
    "verifiable sources support -- never invent a category or inflate size. Respond only via the "
    "requested tool call, as the exact JSON object described."
)

_DRAFT_SYSTEM = (
    "You list a company's REAL, CURRENT competitors as a competitive-analysis seed. Given a target "
    "profile (name, product categories, size/segment), return genuinely-existing companies that "
    "(1) match the target's size tier and customer segment, (2) together cover EVERY product "
    "category in the profile -- not just the dominant one -- and (3) are real (never invent or "
    "fabricate a company; when unsure, return fewer). For each, give a one-line reason, a "
    "size/segment tag, which product category it covers, and a source URL if you know one. "
    "Deduplicate by ultimate parent company: if two names are the same business (an acquisition or "
    "rename), list ONLY the surviving entity. Put clearly larger-scale or up-market players in the "
    "'aspirational' tier, not the direct set. Respond only via the requested tool call, as the "
    "exact JSON object described."
)

_CRITIQUE_SYSTEM = (
    "You are a rigorous reviewer refining a draft competitor list against a target profile. Return "
    "the final, ordered competitor set after: removing duplicates and any renamed or acquired "
    "entity (keep ONLY the surviving company); dropping or demoting competitors whose scale or "
    "customer segment does not match the target (a much larger / enterprise vendor is not a direct "
    "competitor of an SMB shop); and ensuring EVERY product category in the profile is represented "
    "-- if the draft leaves a category uncovered, add a real company that covers it. Never "
    "fabricate a company. Order the strongest direct matches first and cap the list at "
    f"{_MAX_COMPETITORS}. Respond only via the requested tool call, as the exact JSON object "
    "described."
)


@dataclass(frozen=True)
class _TargetProfile:
    """The stage-(a) grounding: brand name, the full product-category set, and a size/segment hint."""

    name: str | None
    categories: list[str]
    size_segment: str | None


@dataclass(frozen=True)
class _Candidate:
    """A stage-(b) draft competitor with the metadata the critique reasons over."""

    name: str
    reason: str | None
    segment: str | None
    category: str | None
    source: str | None
    tier: str  # "direct" | "aspirational"


class BrandSuggestion(BaseModel):
    """The onboarding auto-fill: a proposed brand ``name`` (from the site + web research), the
    echoed ``domain``, and up to ~8 suggested ``competitors`` (names). Every field is a *suggestion*
    the user edits in the wizard -- nothing here is persisted until the user submits ``POST /brands``.
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


def _text_snippet(text: str, max_chars: int) -> str | None:
    """A whitespace-collapsed, length-capped snippet of the page's text (or ``None`` if empty)."""
    snippet = " ".join(text.split())[:max_chars]
    return snippet or None


def _fetch_context(*, domain: str, fetcher: PageFetcher) -> tuple[str | None, str | None]:
    """Fetch the site once (never raising) and derive ``(name_hint, page_text)`` for the profile.

    ``name_hint`` prefers a parsed ``<title>``/``og:site_name``/JSON-LD name (fires when the fetcher
    surfaces raw markup, as fakes do), else a short visible-text snippet; ``page_text`` is a larger
    visible-text excerpt that grounds the category/size profiling. Both are ``None`` when the page
    can't be fetched or is empty -- the profile then leans on the domain (and web search, if any).
    """
    try:
        page = fetcher.fetch(normalize_url(domain))
    except Exception:
        return None, None  # a broken/blocked/timed-out fetch must never break onboarding
    if page is None or not page.text:
        return None, None
    name_hint = _name_from_markup(page.text) or _text_snippet(page.text, _HINT_MAX_CHARS)
    page_text = _text_snippet(page.text, _PAGE_MAX_CHARS)
    return name_hint, page_text


# --- stage (a): target profile ----------------------------------------------------------------


def _build_profile_prompt(*, domain: str, name_hint: str | None, page_text: str | None) -> str:
    lines = [
        f"Website domain: {domain}.",
        "Profile the company that operates this website, for a competitor-analysis seed.",
    ]
    if name_hint:
        lines.append(
            "Name hint read off the site (may be noisy boilerplate -- use only if it helps): "
            f"{name_hint!r}."
        )
    if page_text:
        lines.append(f"Visible page text (excerpt):\n{page_text}")
    lines.append(
        "Return the company's proper 'name', the full list of product 'categories' it operates in "
        "(every distinct one, not just the main line), and a 'size_segment' hint (employee band / "
        "HQ / SMB vs enterprise). If you can search the web, verify against current sources. Report "
        "only what is supported."
    )
    return "\n".join(lines)


def _parse_profile(result: Any) -> _TargetProfile | None:
    """Map a stage-(a) tool-call result to a :class:`_TargetProfile`, or ``None`` if unusable.

    Best-effort: a missing/blank field just drops out (name -> ``None``, categories -> ``[]``); the
    caller degrades accordingly. Categories are trimmed, blank-dropped, and case-insensitively
    deduped while preserving order.
    """
    if not isinstance(result, dict):
        return None
    raw_name = result.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
    raw_seg = result.get("size_segment")
    size_segment = raw_seg.strip() if isinstance(raw_seg, str) and raw_seg.strip() else None
    categories = _clean_names(_str_list(result.get("categories")), brand_name="", cap=32)
    return _TargetProfile(name=name, categories=categories, size_segment=size_segment)


# --- stage (b): web-search-grounded draft ------------------------------------------------------


def _profile_lines(profile: _TargetProfile | None) -> list[str]:
    """The shared profile summary injected into the draft + critique prompts (empty if no profile)."""
    if profile is None:
        return ["(No profile available -- infer the market from the domain.)"]
    lines: list[str] = []
    if profile.name:
        lines.append(f"Target company: {profile.name}.")
    if profile.categories:
        lines.append(
            "Product categories the target operates in (cover EVERY one): "
            + ", ".join(profile.categories)
            + "."
        )
    if profile.size_segment:
        lines.append(f"Target size/segment: {profile.size_segment}.")
    return lines or ["(Profile was empty -- infer the market from the domain.)"]


def _build_draft_prompt(*, domain: str, profile: _TargetProfile | None) -> str:
    lines = [f"Target company domain: {domain}."]
    lines.extend(_profile_lines(profile))
    lines.append(
        f"List up to {_DRAFT_CANDIDATES} real, CURRENT competitors as 'competitors'. For each give "
        "its name, a one-line reason, a size/segment tag ('segment'), which target 'category' it "
        "covers, a 'source' URL if known, and a 'tier' ('direct' if it matches the target's size "
        "and segment, 'aspirational' if clearly larger / up-market). Cover EVERY product category "
        "above. Dedupe by ultimate parent -- list only the surviving entity of any "
        "acquisition/rename. Never invent a company."
    )
    return "\n".join(lines)


def _parse_draft(result: Any) -> list[_Candidate]:
    """Map a stage-(b) tool-call result to a list of :class:`_Candidate`, dropping nameless items.

    Accepts ``{"name", "reason"?, "segment"?, "category"?, "source"?, "tier"?}`` items or bare
    strings (treated as a direct-tier name). ``tier`` defaults to ``"direct"`` when absent/invalid,
    so an un-bucketed candidate is kept in the direct set rather than silently demoted. Any shape it
    doesn't understand yields ``[]`` (the caller treats that like a failed draft).
    """
    if not isinstance(result, dict):
        return []
    raw = result.get("competitors")
    if not isinstance(raw, list):
        return []
    candidates: list[_Candidate] = []
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
            if name:
                candidates.append(_Candidate(name, None, None, None, None, "direct"))
            continue
        if not isinstance(item, dict):
            continue
        raw_name = item.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        tier = item.get("tier")
        candidates.append(
            _Candidate(
                name=raw_name.strip(),
                reason=_opt_str(item.get("reason")),
                segment=_opt_str(item.get("segment")),
                category=_opt_str(item.get("category")),
                source=_opt_str(item.get("source")),
                tier="aspirational" if tier == "aspirational" else "direct",
            )
        )
    return candidates


def _direct_names(draft: list[_Candidate]) -> list[str]:
    """The draft's direct-tier competitor names -- the graceful fallback when the critique fails.

    Excludes ``aspirational`` (clearly-larger) players, so even without the critique the final set
    doesn't include an obvious scale mismatch.
    """
    return [c.name for c in draft if c.tier != "aspirational"]


# --- stage (c): critique -> refine -------------------------------------------------------------


def _build_critique_prompt(*, domain: str, profile: _TargetProfile | None, draft: list[_Candidate]) -> str:
    lines = [f"Target company domain: {domain}."]
    lines.extend(_profile_lines(profile))
    lines.append("Draft competitor list to review:")
    for cand in draft:
        parts = [f"tier={cand.tier}"]
        if cand.segment:
            parts.append(f"segment={cand.segment}")
        if cand.category:
            parts.append(f"category={cand.category}")
        suffix = f" -- {cand.reason}" if cand.reason else ""
        lines.append(f"- {cand.name} [{'; '.join(parts)}]{suffix}")
    lines.append(
        f"Return the final 'competitors' (ordered names, strongest direct match first, max "
        f"{_MAX_COMPETITORS}) after: removing duplicates and any renamed/acquired entity (keep the "
        "surviving company); dropping or demoting competitors whose scale/segment does not match "
        "the target; and ensuring every product category above is represented -- add a real "
        "company for any uncovered category. Optionally add 'coverage_notes'. Never fabricate."
    )
    return "\n".join(lines)


def _parse_critique_names(result: Any) -> list[str]:
    """The final ordered competitor names from a stage-(c) result (accepts str or ``{name}`` items)."""
    if not isinstance(result, dict):
        return []
    return _str_list(result.get("competitors"))


# --- shared helpers ---------------------------------------------------------------------------


def _opt_str(value: Any) -> str | None:
    """A trimmed non-empty string, or ``None``."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _str_list(raw: Any) -> list[str]:
    """Flatten a list of str / ``{"name": str}`` items to trimmed non-empty names (order preserved)."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            candidate = item.strip()
        elif isinstance(item, dict):
            name = item.get("name")
            candidate = name.strip() if isinstance(name, str) else ""
        else:
            candidate = ""
        if candidate:
            out.append(candidate)
    return out


def _clean_names(names: list[str], *, brand_name: str, cap: int) -> list[str]:
    """Deterministic backstop: drop blanks, the brand itself, and case-insensitive dupes; cap length.

    This runs on whatever the LLM stages return, so even a stage that leaks a duplicate or the brand
    name yields a clean list. (Parent-company dedupe -- Nutricap == NutraScience -- needs the LLM's
    world knowledge and is done in the prompts; this only catches exact/case-insensitive repeats.)
    """
    self_key = brand_name.strip().lower()
    seen: set[str] = set()
    out: list[str] = []
    for candidate in names:
        candidate = candidate.strip()
        key = candidate.lower()
        if not candidate or (self_key and key == self_key) or key in seen:
            continue
        seen.add(key)
        out.append(candidate)
        if len(out) >= cap:
            break
    return out


# --- progress emission (log every stage; notify the optional hook, total) ----------------------


def _emit(on_progress: ProgressHook | None, stage_key: str) -> None:
    """Log ``stage_key`` (so the backend terminal shows progress) and, if set, notify ``on_progress``.

    Total, matching the module's never-raise guarantee: a raising hook is swallowed (a dropped
    progress update is harmless), so instrumentation can never break the suggestion pipeline.
    """
    label = _STAGE_LABELS[stage_key]
    logger.info("suggest_brand_details: stage=%s (%s)", stage_key, label)
    if on_progress is not None:
        try:
            on_progress(stage_key, label)
        except Exception:
            logger.exception("on_progress hook raised for stage=%s", stage_key)


# --- pipeline stage runners (each total: catches every failure, returns a safe default) --------


def _run_profile(
    *, domain: str, name_hint: str | None, page_text: str | None, llm: LLMClient
) -> _TargetProfile | None:
    """Stage (a): one web-search-grounded profiling call; ``None`` on any failure (never raises)."""
    try:
        result = llm.complete(
            system=_PROFILE_SYSTEM,
            prompt=_build_profile_prompt(domain=domain, name_hint=name_hint, page_text=page_text),
            schema=_PROFILE_SCHEMA,
        )
    except Exception:
        return None
    return _parse_profile(result)


def _run_draft(*, domain: str, profile: _TargetProfile | None, llm: LLMClient) -> list[_Candidate]:
    """Stage (b): one web-search-grounded draft call; ``[]`` on any failure (never raises)."""
    try:
        result = llm.complete(
            system=_DRAFT_SYSTEM,
            prompt=_build_draft_prompt(domain=domain, profile=profile),
            schema=_DRAFT_SCHEMA,
        )
    except Exception:
        return []
    return _parse_draft(result)


def _run_critique(
    *, domain: str, profile: _TargetProfile | None, draft: list[_Candidate], critic: LLMClient
) -> list[str]:
    """Stage (c): one refine call over the draft; ``[]`` on any failure (never raises).

    Skipped (``[]``) when the draft is empty -- there is nothing to refine and the caller degrades
    to no competitors, matching the total-failure behavior.
    """
    if not draft:
        return []
    try:
        result = critic.complete(
            system=_CRITIQUE_SYSTEM,
            prompt=_build_critique_prompt(domain=domain, profile=profile, draft=draft),
            schema=_CRITIQUE_SCHEMA,
        )
    except Exception:
        return []
    return _parse_critique_names(result)


def suggest_brand_details(
    *,
    domain: str,
    fetcher: PageFetcher,
    llm: LLMClient,
    critic: LLMClient | None = None,
    on_progress: ProgressHook | None = None,
) -> BrandSuggestion:
    """Propose a brand ``name`` + grounded, self-critiqued ``competitors`` for a bare ``domain``.

    Runs the three-stage pipeline -- profile (a) -> web-search-grounded draft (b) -> critique/refine
    (c) -- on the injected clients. ``llm`` drives the research/draft stages (web-search-enabled on
    the local-Claude gateway); ``critic`` drives the (web-search-free) refine stage, defaulting to
    ``llm`` when omitted. The brand name comes from the profile (else the domain heuristic); the
    competitor list is the critique's refined output (else the draft's direct-tier names), run
    through a deterministic dedupe/self-exclude/cap backstop.

    ``on_progress`` (optional, backward-compatible: ``None`` -> today's behavior) is called at the
    **start** of each stage with ``(stage_key, human_label)`` -- ``fetching`` -> ``profiling`` ->
    ``researching`` -> ``refining`` -> ``done`` -- so a caller (the async job endpoint) can stream
    live progress instead of holding a ~1-2 min HTTP connection. Every stage is also ``logger.info``
    -ed regardless, so the backend terminal shows progress. A raising hook never breaks the run.

    Best-effort and total: a fetch failure drops the grounding, any failed/empty/malformed LLM stage
    degrades to the best available result (ultimately the domain-heuristic name + ``[]``), and the
    function never raises -- so onboarding always yields a usable, fully-editable suggestion and
    never surfaces a 5xx.
    """
    clean_domain = domain.strip()
    critic_client = critic if critic is not None else llm

    _emit(on_progress, "fetching")
    name_hint, page_text = _fetch_context(domain=clean_domain, fetcher=fetcher)

    _emit(on_progress, "profiling")
    profile = _run_profile(
        domain=clean_domain, name_hint=name_hint, page_text=page_text, llm=llm
    )

    _emit(on_progress, "researching")
    draft = _run_draft(domain=clean_domain, profile=profile, llm=llm)

    _emit(on_progress, "refining")
    refined = _run_critique(
        domain=clean_domain, profile=profile, draft=draft, critic=critic_client
    )

    name = (profile.name if profile and profile.name else None) or _name_from_domain(clean_domain)
    # The critique's refined list wins; if it failed/was empty, fall back to the draft's direct set
    # (aspirational/up-market players already excluded) -- then a deterministic clean-up backstop.
    chosen = refined if refined else _direct_names(draft)
    competitors = _clean_names(chosen, brand_name=name, cap=_MAX_COMPETITORS)
    suggestion = BrandSuggestion(name=name, domain=clean_domain, competitors=competitors)

    _emit(on_progress, "done")
    return suggestion


__all__ = ["BrandSuggestion", "normalize_url", "suggest_brand_details"]
