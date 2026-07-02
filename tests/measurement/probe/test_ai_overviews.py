"""Tests for the Google AI Overviews adapter (docs/tasks/M1-T11-ai-overviews-adapter.md).

Hermetic: `FakeCaptureClient` serves a recorded HTML fixture, so no browser/network is ever
touched. Covers the `EngineAdapter` contract shape plus the DOM-resilience requirement from
m1-design.md §10 -- garbled/missing-overview HTML must degrade to an empty result, never raise.
"""

import pathlib

import pytest

from gw_geo.capture.base import CapturePage
from gw_geo.common.models import ProbeResult
from gw_geo.measurement.probe.ai_overviews import AIOverviewsAdapter
from gw_geo.measurement.probe.base import EngineAdapter
from tests.capture.fakes import FakeCaptureClient

_FIXTURES_DIR = pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "answers"
HTML = (_FIXTURES_DIR / "google_ai_overviews.html").read_text()

_SEARCH_URL = "https://www.google.com/search?q=best+crm"


def _capture_for(html: str) -> FakeCaptureClient:
    return FakeCaptureClient({"google_ai_overviews": CapturePage(html=html, final_url=_SEARCH_URL)})


async def test_probe_parses_overview_and_sources():
    adapter = AIOverviewsAdapter(capture=_capture_for(HTML))

    assert isinstance(adapter, EngineAdapter)
    assert adapter.name == "google_ai_overviews"
    assert adapter.supports_citations is True

    result = await adapter.probe("best crm for smb?")

    assert isinstance(result, ProbeResult)
    assert result.engine == "google_ai_overviews"
    assert result.answer_text
    assert "HubSpot" in result.answer_text
    assert result.cited_urls
    assert all(u.startswith("http") for u in result.cited_urls)
    # The HubSpot link appears both inline in the answer and in the source list -- de-duped.
    assert len(result.cited_urls) == len(set(result.cited_urls))
    assert result.cited_urls == [
        "https://www.hubspot.com/products/crm",
        "https://www.g2.com/categories/crm",
        "https://www.salesforce.com/crm/",
        "https://www.zoho.com/crm/pricing",
    ]
    # A link outside the AI Overview container must never be treated as a citation.
    assert "https://example.com/other-organic-result" not in result.cited_urls


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
        "<html><body><div><span>not an overview<div>unclosed tags</span></body>",
        "<html><body><p>Totally redesigned SERP with no overview widget.</p></body></html>",
    ],
    ids=["empty", "malformed", "no-overview-node"],
)
async def test_probe_is_resilient_to_garbled_or_missing_overview_html(garbled_html: str) -> None:
    adapter = AIOverviewsAdapter(capture=_capture_for(garbled_html))

    result = await adapter.probe("best crm for smb?")

    assert result.engine == "google_ai_overviews"
    assert result.answer_text == ""
    assert result.cited_urls == []
