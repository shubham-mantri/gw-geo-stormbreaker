"""Google AI Overviews adapter (m1-design.md §S3.1, docs/tasks/M1-T11-ai-overviews-adapter.md).

Playwright-surface adapter: calls the injected `CaptureClient` to fetch a rendered Google search
results page for `prompt`, then parses the returned `CapturePage.html` with BeautifulSoup to pull
out the AI Overview's answer text and cited source URLs.

The consumer-facing AI Overview DOM has no public/stable API and Google changes its markup
without notice (m1-design.md §10: "consumer-surface DOM is unstable -> parsers must be
resilient"). Every lookup below walks a short priority list of candidate selectors and degrades
toward an empty/partial result on a miss instead of raising, so a DOM change shows up as reduced
answer/citation quality rather than a broken measurement run. See
`tests/measurement/probe/test_ai_overviews.py` for the pinned garbled/missing-overview behavior.

Import is side-effect-free: this module never calls `measurement.probe.base.register()` itself --
that happens at wiring time (runner/CLI), same convention as the other adapters.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from gw_geo.capture.base import CaptureClient
from gw_geo.common.models import ProbeResult

# Candidate selectors for the AI Overview's outer container, tried in priority order until one
# matches. Scoping every lookup below to this root -- rather than scanning the whole SERP -- is
# what keeps unrelated organic-result links from being mistaken for AI Overview citations.
_ROOT_SELECTORS: tuple[str, ...] = ('[data-attrid="AIOverview"]', ".LT6Yjf")

# Candidate selectors for the answer-text node *within* the root, most-specific first.
_ANSWER_SELECTORS: tuple[str, ...] = ('[jsname="aioAnswer"]', ".AIOverview-answer", ".hgKElc")

# Candidate selectors for the sources/citations node *within* the root.
_SOURCES_SELECTORS: tuple[str, ...] = ('[jsname="aioSources"]', ".AIOverview-sources")


def _find_first(scope: Tag, selectors: tuple[str, ...]) -> Tag | None:
    """Return the first descendant of `scope` matched by any selector, tried in order."""
    for selector in selectors:
        match = scope.select_one(selector)
        if match is not None:
            return match
    return None


def _normalize_url(href: str) -> str | None:
    """Resolve an anchor `href` to an absolute http(s) URL, or None if it isn't citable.

    Handles direct `http(s)://...` hrefs and Google's `/url?q=<dest>&...` redirect wrapper.
    Relative links, `#fragment`, `javascript:`, `mailto:`, and empty hrefs are all dropped --
    citation extraction is best-effort and must never be a source of parse errors.
    """
    href = href.strip()
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("/url?"):
        dest = parse_qs(urlparse(href).query).get("q", [""])[0]
        if dest.startswith(("http://", "https://")):
            return dest
    return None


def _extract_cited_urls(*containers: Tag | None) -> list[str]:
    """Collect de-duped, normalized citation URLs from `<a href>` tags in `containers`.

    First-seen order is preserved across `containers` in the order given. Missing (`None`)
    containers are skipped rather than raising, and containers may overlap (e.g. passing both
    a sub-node and its ancestor) -- de-duplication makes that safe, so callers can freely stack
    a specific lookup with a broader fallback for resilience against renamed/missing nodes.
    """
    seen: set[str] = set()
    urls: list[str] = []
    for container in containers:
        if container is None:
            continue
        for anchor in container.find_all("a", href=True):
            normalized = _normalize_url(str(anchor["href"]))
            if normalized is not None and normalized not in seen:
                seen.add(normalized)
                urls.append(normalized)
    return urls


class AIOverviewsAdapter:
    """`EngineAdapter` for Google's AI Overviews, driven through the Playwright capture seam."""

    name = "google_ai_overviews"
    supports_citations = True

    def __init__(self, capture: CaptureClient) -> None:
        self._capture = capture

    async def probe(
        self, prompt: str, *, geo: str = "us", persona: str | None = None
    ) -> ProbeResult:
        """Fetch a rendered SERP for `prompt` and parse its AI Overview into a `ProbeResult`.

        `geo`/`persona` flow straight through to the capturer, which owns proxy-geo and
        account-persona targeting (T09/T10) -- this adapter only parses the resulting page.
        Never raises on unexpected, renamed, or missing DOM (m1-design.md §10): the worst case
        is an empty `answer_text` and `cited_urls == []`.
        """
        started = time.perf_counter()
        page = await self._capture.fetch(prompt, surface=self.name, geo=geo, persona=persona)
        latency_ms = int((time.perf_counter() - started) * 1000)

        soup = BeautifulSoup(page.html, "html.parser")
        root = _find_first(soup, _ROOT_SELECTORS)

        answer_text = ""
        cited_urls: list[str] = []
        if root is not None:
            answer_node = _find_first(root, _ANSWER_SELECTORS)
            sources_node = _find_first(root, _SOURCES_SELECTORS)
            # Renamed/restructured answer node: fall back to the root's own text rather than an
            # empty string, since the overview clearly exists -- just not where expected.
            answer_text = (answer_node or root).get_text(" ", strip=True)
            # Root is included as a trailing fallback so a renamed/missing answer or sources
            # node still surfaces any citation links left directly under the overview root;
            # de-duplication makes the overlap with answer_node/sources_node harmless.
            cited_urls = _extract_cited_urls(answer_node, sources_node, root)

        return ProbeResult(
            engine=self.name,
            answer_text=answer_text,
            cited_urls=cited_urls,
            raw={"html": page.html, "final_url": page.final_url},
            latency_ms=latency_ms,
        )
