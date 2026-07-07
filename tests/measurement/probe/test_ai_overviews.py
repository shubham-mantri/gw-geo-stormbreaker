"""Tests for the Google AI-Mode adapter (docs/tasks/M1-T11-ai-overviews-adapter.md).

Hermetic: `FakeCaptureClient` serves a recorded AI-Mode HTML fixture, so no browser/network is ever
touched. Covers the `EngineAdapter` contract shape, the AI-Mode extraction (answer from
`[role="main"]`; citations = external non-Google links only), and the DOM-resilience requirement
from m1-design.md §10 -- garbled/missing DOM must degrade to a partial/empty result, never raise.
"""

import pathlib

import pytest

from gw_geo.capture.base import CapturePage
from gw_geo.common.models import ProbeResult
from gw_geo.measurement.probe.ai_overviews import AIOverviewsAdapter, _extract_answer
from gw_geo.measurement.probe.base import EngineAdapter
from tests.capture.fakes import FakeCaptureClient

_FIXTURES_DIR = pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "answers"
HTML = (_FIXTURES_DIR / "google_ai_overviews.html").read_text()

_SEARCH_URL = "https://www.google.com/search?q=best+crm&udm=50"

# The external citations the fixture's AI-Mode answer links to, in document order. Google's own
# nav/asset links, a `/url?` redirect wrapper (a google host, NOT unwrapped), and the duplicate
# direct HubSpot chip are all excluded.
_EXPECTED_CITATIONS = [
    "https://www.hubspot.com/products/crm",
    "https://www.salesforce.com/crm/",
    "https://www.g2.com/categories/crm",
    "https://www.zoho.com/crm/pricing",
]


def _capture_for(html: str) -> FakeCaptureClient:
    return FakeCaptureClient({"google_ai_overviews": CapturePage(html=html, final_url=_SEARCH_URL)})


async def test_probe_parses_ai_mode_answer_and_external_citations():
    adapter = AIOverviewsAdapter(capture=_capture_for(HTML))

    assert isinstance(adapter, EngineAdapter)
    assert adapter.name == "google_ai_overviews"
    assert adapter.supports_citations is True

    result = await adapter.probe("best crm for smb?")

    assert isinstance(result, ProbeResult)
    assert result.engine == "google_ai_overviews"
    assert result.answer_text
    assert "HubSpot" in result.answer_text
    assert result.cited_urls == _EXPECTED_CITATIONS
    # De-duped (the direct HubSpot link appears twice in the fixture).
    assert len(result.cited_urls) == len(set(result.cited_urls))
    # Google's own chrome -- nav, assets, and the `/url?` redirect wrapper -- is never a citation.
    assert all("google" not in u and "gstatic" not in u for u in result.cited_urls)


def test_extract_answer_reads_main_region_and_isolates_external_citations():
    answer_text, cited_urls = _extract_answer(HTML, base_url=_SEARCH_URL)

    # Answer text comes from the `[role="main"]` region.
    assert "HubSpot" in answer_text
    assert "Salesforce" in answer_text
    assert cited_urls == _EXPECTED_CITATIONS


def test_extract_answer_falls_back_to_whole_document_without_main_region():
    """No `[role="main"]` -> use the whole document's text; external links still surface."""
    answer_text, cited_urls = _extract_answer(
        "<html><body><p>No main region, just "
        '<a href="https://example.com/x">Example</a> and '
        '<a href="https://www.google.com/foo">Google</a>.</p></body></html>'
    )

    assert "No main region" in answer_text
    assert cited_urls == ["https://example.com/x"]  # google host dropped even without a main region


def test_extract_answer_resolves_relative_external_links_against_base_url():
    """A relative href on a non-Google-derived base URL normalizes to an absolute citation."""
    answer_text, cited_urls = _extract_answer(
        '<div role="main"><a href="/page">rel</a></div>', base_url="https://vendor.example/blog"
    )

    assert cited_urls == ["https://vendor.example/page"]


async def test_probe_passes_prompt_geo_and_persona_through_to_capture():
    seen: dict[str, object] = {}

    class RecordingCaptureClient:
        async def fetch(
            self, query: str, *, surface: str, geo: str, persona: str | None
        ) -> CapturePage:
            seen.update(query=query, surface=surface, geo=geo, persona=persona)
            return CapturePage(html=HTML, final_url=_SEARCH_URL)

    adapter = AIOverviewsAdapter(capture=RecordingCaptureClient())

    await adapter.probe("best crm for smb?", geo="uk", persona="smb_buyer")

    assert seen == {
        "query": "best crm for smb?",
        "surface": "google_ai_overviews",
        "geo": "uk",
        "persona": "smb_buyer",
    }


@pytest.mark.parametrize(
    "garbled_html",
    [
        "",
        "<html><body><div><span>not an answer<div>unclosed tags</span></body>",
        "<html><body><p>Totally redesigned SERP with no AI Mode region.</p></body></html>",
    ],
    ids=["empty", "malformed", "no-main-region"],
)
async def test_probe_is_resilient_to_garbled_or_missing_dom(garbled_html: str) -> None:
    adapter = AIOverviewsAdapter(capture=_capture_for(garbled_html))

    result = await adapter.probe("best crm for smb?")

    assert result.engine == "google_ai_overviews"
    # None of these contain an external anchor -> no citations, and extraction never raises.
    assert result.cited_urls == []


async def test_probe_empty_html_yields_empty_answer_and_citations() -> None:
    """The fully-degraded case: empty DOM -> empty answer + no citations, never raises."""
    adapter = AIOverviewsAdapter(capture=_capture_for(""))

    result = await adapter.probe("best crm for smb?")

    assert result.answer_text == ""
    assert result.cited_urls == []
