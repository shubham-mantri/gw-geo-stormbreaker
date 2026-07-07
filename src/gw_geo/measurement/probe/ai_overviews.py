"""Google AI-Mode adapter (m1-design.md §S3.1, docs/tasks/M1-T11-ai-overviews-adapter.md).

Playwright-surface adapter: calls the injected `CaptureClient` to fetch a rendered Google **AI
Mode** results page for `prompt` (the local capture path navigates directly to
`/search?q=...&udm=50`, see `capture/local.py`), then parses the returned `CapturePage.html` with
BeautifulSoup to pull out the AI answer text and its cited external source URLs. The
`google_ai_overviews` engine name is retained for registry/engine-name stability; only the
captured surface changed (AI Overviews box -> AI Mode).

The consumer-facing DOM has no public/stable API and Google changes its markup without notice
(m1-design.md §10: "consumer-surface DOM is unstable -> parsers must be resilient"). Rather than
keying on Google's obfuscated, churning class names (e.g. citation anchors carry classes like
`PMDqCb` that change without notice), extraction is deliberately structure-light: the answer is the
text of the page's `[role="main"]` region, and citations are simply every external (non-Google)
http(s) link on the page. A DOM change therefore shows up as reduced answer/citation quality rather
than a broken run. See `tests/measurement/probe/test_ai_overviews.py`.

Import is side-effect-free: this module never calls `measurement.probe.base.register()` itself --
that happens at wiring time (runner/CLI), same convention as the other adapters.
"""

from __future__ import annotations

import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from gw_geo.capture.base import CaptureClient
from gw_geo.common.models import ProbeResult

# The region AI Mode streams its answer into. If absent (a redesign, or a non-AI-Mode SERP), we
# fall back to the whole document's text rather than an empty answer.
_MAIN_SELECTOR = "[role='main']"

# Host substrings identifying Google's own chrome -- navigation, static assets, image/user-content
# proxies, tracking, `/url?` redirect wrappers -- as opposed to a cited external source. Any anchor
# whose host matches one of these is dropped, which is what reliably isolates the real external
# citations from the dozens of internal links Google renders on the page. Matched as a substring of
# the lowercased host so subdomains and ccTLDs are all covered (e.g. `www.google.com`,
# `news.google.co.uk`, `encrypted-tbn0.gstatic.com`, `lh3.googleusercontent.com`).
_GOOGLE_HOST_MARKERS: tuple[str, ...] = (
    "google.",
    "gstatic.",
    "googleusercontent.",
    "googleapis.",
    "googlesyndication.",
    "googleadservices.",
    "ggpht.",
)


def _is_google_host(host: str) -> bool:
    """True if `host` belongs to Google's own chrome (not a cited external source)."""
    host = host.lower()
    return any(marker in host for marker in _GOOGLE_HOST_MARKERS)


def _external_citation(href: str, *, base_url: str) -> str | None:
    """Resolve `href` to an absolute http(s) URL if it points to a non-Google host, else None.

    Relative hrefs resolve against `base_url` (the captured page's final URL); Google's own links
    -- relative nav, `/url?q=...` redirect wrappers, gstatic/googleusercontent assets -- all resolve
    to a Google host and are dropped, as are `#fragment`, `javascript:`, and `mailto:` (non-http).
    """
    absolute = urljoin(base_url, href.strip())
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if not host or _is_google_host(host):
        return None
    return absolute


def _extract_answer(html: str, *, base_url: str = "") -> tuple[str, list[str]]:
    """Parse an AI-Mode results page into (answer_text, external_cited_urls). Never raises.

    `answer_text` is the text of the `[role="main"]` region; real AI Mode leaves that landmark
    EMPTY and streams the answer into obfuscated-class divs, so when it is empty (or absent) we fall
    back to the `<body>` (whole-document) text rather than returning nothing. `cited_urls` is every
    external http(s) link on the page -- excluding Google's own nav/asset/tracking/redirect domains
    -- normalized to absolute, de-duped, and kept in document order. A garbled DOM degrades to ("", []).
    """
    soup = BeautifulSoup(html, "html.parser")

    main = soup.select_one(_MAIN_SELECTOR)
    answer_text = main.get_text(" ", strip=True) if main is not None else ""
    if not answer_text:
        answer_text = (soup.body or soup).get_text(" ", strip=True)

    seen: set[str] = set()
    cited_urls: list[str] = []
    for anchor in soup.find_all("a", href=True):
        url = _external_citation(str(anchor["href"]), base_url=base_url)
        if url is not None and url not in seen:
            seen.add(url)
            cited_urls.append(url)
    return answer_text, cited_urls


class AIOverviewsAdapter:
    """`EngineAdapter` for Google's AI Mode, driven through the Playwright capture seam."""

    name = "google_ai_overviews"
    supports_citations = True

    def __init__(self, capture: CaptureClient) -> None:
        self._capture = capture

    async def probe(
        self, prompt: str, *, geo: str = "us", persona: str | None = None
    ) -> ProbeResult:
        """Fetch a rendered AI-Mode SERP for `prompt` and parse it into a `ProbeResult`.

        `geo`/`persona` flow straight through to the capturer, which owns proxy-geo and
        account-persona targeting (T09/T10) -- this adapter only parses the resulting page.
        Never raises on unexpected, renamed, or missing DOM (m1-design.md §10): the worst case
        is an empty `answer_text` and `cited_urls == []`.
        """
        started = time.perf_counter()
        page = await self._capture.fetch(prompt, surface=self.name, geo=geo, persona=persona)
        latency_ms = int((time.perf_counter() - started) * 1000)

        answer_text, cited_urls = _extract_answer(page.html, base_url=page.final_url)

        return ProbeResult(
            engine=self.name,
            answer_text=answer_text,
            cited_urls=cited_urls,
            raw={"html": page.html, "final_url": page.final_url},
            latency_ms=latency_ms,
        )
