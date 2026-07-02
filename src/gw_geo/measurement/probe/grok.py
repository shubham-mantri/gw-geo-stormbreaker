"""Grok engine adapter (m1-design.md §3.2) — Playwright-surface DOM parser -> `ProbeResult`.

Grok's consumer surface (grok.com / x.com's Grok tab) has no citation-returning API, so this
adapter drives it through the `CaptureClient` seam (m1-design.md §3.1): `probe()` calls
`capture.fetch(...)` for a `CapturePage` (a recorded HTML fixture in tests, a live Playwright
capture in production) and parses `CapturePage.html` into `answer_text` + `cited_urls`.

The consumer DOM is unstable (m1-design.md §10 -- "parsers must be resilient"), so this parser
never raises on missing/renamed nodes: `_ANSWER_SELECTORS` / `_CITATIONS_SELECTORS` are tried in
order and a miss degrades to best-effort text / an empty citation list rather than an exception.
`BeautifulSoup`'s stdlib `html.parser` backend (no extra parser dependency beyond `beautifulsoup4`,
already a project dependency) is itself lenient and does not raise on malformed markup, so no
adapter-level try/except is needed to satisfy that requirement either.

Import is side-effect-free: this module never calls `measurement.probe.base.register()` itself --
that happens at wiring time (runner/CLI), same convention as the other adapters.
"""

from __future__ import annotations

import time
from urllib.parse import urldefrag

from bs4 import BeautifulSoup, Tag

from gw_geo.capture.base import CaptureClient
from gw_geo.common.models import ProbeResult

# Tried in order; first match wins. The consumer DOM is unstable, so this list carries both a
# "current" selector and plausible fallbacks a redesign might land on.
_ANSWER_SELECTORS = (
    '[data-testid="grok-answer"]',
    'main [data-testid="answer"]',
    "div.grok-answer",
    "article",
)
_CITATIONS_SELECTORS = (
    '[data-testid="grok-citations"]',
    "div.grok-sources",
    "aside.citations",
    "ol.sources",
)


def _first_match(node: Tag, selectors: tuple[str, ...]) -> Tag | None:
    """Return the first element under `node` matching any selector in `selectors`, else `None`."""
    for selector in selectors:
        match = node.select_one(selector)
        if match is not None:
            return match
    return None


def _normalize_url(url: str) -> str:
    """Strip the fragment and a trailing slash so equivalent citation URLs de-dupe cleanly."""
    stripped, _fragment = urldefrag(url.strip())
    if len(stripped) > 1 and stripped.endswith("/"):
        stripped = stripped.rstrip("/")
    return stripped


def _extract_answer_text(soup: BeautifulSoup) -> str:
    """Best-effort answer text: the answer container if found, else the whole document's text."""
    node = _first_match(soup, _ANSWER_SELECTORS)
    if node is None:
        return soup.get_text(" ", strip=True)
    return node.get_text(" ", strip=True)


def _extract_cited_urls(soup: BeautifulSoup) -> list[str]:
    """Normalized, de-duped, order-preserving `http(s)` links scoped to the citations container.

    Returns an empty list (never raises) when no citations container matches any candidate
    selector -- the m1-design.md §10 resilience requirement for an unstable consumer DOM.
    """
    container = _first_match(soup, _CITATIONS_SELECTORS)
    if container is None:
        return []

    seen: set[str] = set()
    cited_urls: list[str] = []
    for anchor in container.find_all("a", href=True):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        href = href.strip()
        if not href.startswith("http"):
            continue
        normalized = _normalize_url(href)
        if normalized not in seen:
            seen.add(normalized)
            cited_urls.append(normalized)
    return cited_urls


class GrokAdapter:
    """`EngineAdapter` for Grok's consumer surface, driven via the `CaptureClient` seam."""

    name = "grok"
    supports_citations = True

    def __init__(self, capture: CaptureClient) -> None:
        self._capture = capture

    async def probe(
        self, prompt: str, *, geo: str = "us", persona: str | None = None
    ) -> ProbeResult:
        """Fetch the Grok surface via `capture` and parse its DOM into a `ProbeResult`.

        `geo`/`persona` flow straight through to the capturer (proxy geo / authenticated
        account persona, m1-design.md §3.1); this adapter's own job is purely DOM -> `ProbeResult`.
        """
        started = time.perf_counter()
        page = await self._capture.fetch(prompt, surface=self.name, geo=geo, persona=persona)
        latency_ms = int((time.perf_counter() - started) * 1000)

        soup = BeautifulSoup(page.html, "html.parser")
        answer_text = _extract_answer_text(soup)
        cited_urls = _extract_cited_urls(soup)

        return ProbeResult(
            engine=self.name,
            answer_text=answer_text,
            cited_urls=cited_urls,
            raw={"html": page.html, "final_url": page.final_url, "meta": page.meta},
            latency_ms=latency_ms,
            cost_usd=0.0,
        )
