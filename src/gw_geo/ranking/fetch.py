"""Page fetching for the candidate-sourcing crawler (M5): public-URL text + publish date.

The ranking pipeline (`ranking/runner.py`) trains a per-engine model on candidate URLs carrying a
`FeatureVector` (`ranking/features.py`). Those features need the candidate's *content* (for
`structure_score`/`info_density`/`embedding_similarity`/...) and its *publish date* (for
`freshness_days`). This module is the one place that turns a public URL into exactly those two
things, and the only place in the M5 crawler that touches the network.

Two-layer design, mirroring `measurement/parse.py`'s pure/live split and `content.kb`'s injected
client seam:

- `PageFetcher` (Protocol) + `FetchedPage` (the value object) are the seam every consumer
  (`ranking/sourcing.py`) depends on. Consumers never construct a real fetcher themselves -- one is
  **injected**, so hermetic tests pass a trivial dict-backed fake and make no HTTP call at all.
- `HttpxPageFetcher` is the real, live-HTTP implementation. It fetches with redirects followed and
  a bounded timeout, and treats *every* failure mode (timeout, transport error, any non-2xx status)
  as "no page" -- returning `None` rather than raising -- so one dead link never crashes a crawl.
  Visible text is extracted with BeautifulSoup (script/style/etc. stripped); the publish date is
  read from JSON-LD `datePublished` (incl. nested `@graph`) or a `<meta>` published-time tag, and
  is normalized to a `freshness_days`-parseable ISO date (or dropped) so a garbage date value can
  never crash feature extraction downstream.

LOCAL-ONLY / white-hat: this fetches ordinary public URLs (the ones AI engines already cited) with
an honest User-Agent. It is not a SERP/search API and adds no cloud dependency (PRD NG1).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Protocol

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag
from pydantic import BaseModel

# A plain, honest User-Agent (white-hat, PRD NG1) -- we identify ourselves rather than spoofing a
# browser. Overridable per fetcher instance.
_DEFAULT_USER_AGENT = "gw-geo-stormbreaker/1.0 (+https://gushwork.ai; GEO content crawler)"
_DEFAULT_TIMEOUT_S = 10.0

# HTML nodes whose text is never "visible" page content -- stripped before `get_text`.
_NON_CONTENT_TAGS = ("script", "style", "noscript", "template", "head")

# `<meta>` published-date carriers, in priority order (OpenGraph article time first, then the
# schema.org itemprop/name variants). Each is a `(attr, value)` selector matched case-insensitively.
_META_DATE_SELECTORS: tuple[tuple[str, str], ...] = (
    ("property", "article:published_time"),
    ("name", "article:published_time"),
    ("itemprop", "datePublished"),
    ("name", "datePublished"),
    ("property", "og:published_time"),
    ("name", "date"),
)


class FetchedPage(BaseModel):
    """The two things feature extraction needs from a candidate URL.

    `text` is the page's visible text (script/style stripped); `published_at` is an ISO date
    string (`YYYY-MM-DD`) when a publish date could be read and parsed, else `None`. Deliberately
    minimal -- everything else a `FeatureVector` needs is derived from these two by
    `ranking.features.extract_features`.
    """

    text: str
    published_at: str | None = None


class PageFetcher(Protocol):
    """Anything that can turn a public URL into a `FetchedPage` (or `None` if it can't be fetched).

    The injected seam for the candidate-sourcing crawler: `HttpxPageFetcher` is the real
    implementation; hermetic tests inject a dict-backed fake so `sourcing.py` never makes a live
    HTTP call (TRD §12).
    """

    def fetch(self, url: str) -> FetchedPage | None: ...


def _safe_iso_date(value: str | None) -> str | None:
    """Normalize a raw date string to a `YYYY-MM-DD` ISO date, or `None` if it can't be parsed.

    Mirrors `ranking.features._parse_date` (date-only or full ISO-8601, `Z`-suffix tolerated) but is
    *total*: an unparseable value (`"last Tuesday"`, an empty string, a non-ISO format) yields
    `None` rather than raising. This is the guarantee that lets the fetcher hand `published_at`
    straight to `freshness_days` -- which does raise on a bad string -- without ever crashing a crawl.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def _visible_text(soup: BeautifulSoup) -> str:
    """Extract the page's visible text: strip non-content nodes, then collapse whitespace.

    Note (documented limitation, M5): this returns *visible text*, so HTML-tag-shaped feature
    signals (`has_schema`'s `application/ld+json`, `<table>`, `<h1>`) are not preserved -- the
    text-level features (`info_density`, `embedding_similarity`, `freshness_days`,
    `domain_authority`, `corroboration_count`) carry the signal for a crawled candidate.
    """
    for tag in soup(list(_NON_CONTENT_TAGS)):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def _find_date_published(obj: Any) -> str | None:
    """Depth-first search a parsed JSON-LD object for the first `datePublished` string value.

    JSON-LD blocks are often a single object, sometimes a list of them, and often wrap the real
    entity inside an `@graph` array -- so recurse through dicts and lists rather than only reading
    the top level.
    """
    if isinstance(obj, dict):
        value = obj.get("datePublished")
        if isinstance(value, str):
            return value
        for nested in obj.values():
            found = _find_date_published(nested)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_date_published(item)
            if found is not None:
                return found
    return None


def _json_ld_date(soup: BeautifulSoup) -> str | None:
    """The first parseable `datePublished` from any `application/ld+json` block, else `None`."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        candidate = _safe_iso_date(_find_date_published(data))
        if candidate is not None:
            return candidate
    return None


def _meta_date(soup: BeautifulSoup) -> str | None:
    """The first parseable published date from a known `<meta>` published-time tag, else `None`."""
    for attr, wanted in _META_DATE_SELECTORS:
        for meta in soup.find_all("meta", attrs={attr: wanted}):
            if not isinstance(meta, Tag):
                continue
            content = meta.get("content")
            candidate = _safe_iso_date(content if isinstance(content, str) else None)
            if candidate is not None:
                return candidate
    return None


def _extract_published_at(soup: BeautifulSoup) -> str | None:
    """Best-effort publish date: JSON-LD first (most reliable), then a `<meta>` tag; else `None`."""
    return _json_ld_date(soup) or _meta_date(soup)


class HttpxPageFetcher:
    """`PageFetcher` backed by a direct `httpx.get` (redirects followed, bounded timeout).

    Every failure mode -- a timeout, any transport/connection error, or any non-2xx status
    (`raise_for_status`) -- is caught as `httpx.HTTPError` and returned as `None`, so a dead or slow
    candidate link is simply skipped, never fatal to the crawl. Not exercised by the hermetic suite
    against a real host: tests drive it through `respx` (in-process transport mock) or inject a fake.
    """

    def __init__(
        self,
        *,
        timeout: float = _DEFAULT_TIMEOUT_S,
        user_agent: str = _DEFAULT_USER_AGENT,
    ) -> None:
        self._timeout = timeout
        self._user_agent = user_agent

    def fetch(self, url: str) -> FetchedPage | None:
        try:
            response = httpx.get(
                url,
                follow_redirects=True,
                timeout=self._timeout,
                headers={"User-Agent": self._user_agent},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        soup = BeautifulSoup(response.text, "html.parser")
        # Read the publish date BEFORE `_visible_text` decomposes <head>/<script>/<meta> -- those
        # are exactly the nodes the date lives in, so stripping them first would lose it.
        published_at = _extract_published_at(soup)
        return FetchedPage(text=_visible_text(soup), published_at=published_at)
