"""Spec test for the consumer ChatGPT UI adapter (docs/tasks/M1-T12-chatgpt-adapter.md).

Naming note: the task file calls the module/fixture `chatgpt_ui` (`.py`/`.html`); this wave's
orchestrator renamed the surface's files to `chatgpt.py` / `chatgpt.html` (the adapter's `name`,
`"chatgpt"`, already disambiguates it from the M0 API adapter `name = "openai"`). The T10 contract
suite row for this adapter is deferred to T19 (consolidated there to avoid parallel-wave merge
conflicts on the shared `test_adapter_contract.py` / `fixtures.py`), so this module owns its own
`EngineAdapter` + `ProbeResult` contract checks. Every assertion mirrors the task spec's test body.
"""

import pathlib

import pytest

from gw_geo.capture.base import CapturePage
from gw_geo.common.models import ProbeResult
from gw_geo.measurement.probe.base import EngineAdapter
from gw_geo.measurement.probe.chatgpt import ChatGPTAdapter
from tests.capture.fakes import FakeCaptureClient

HTML = pathlib.Path("tests/fixtures/answers/chatgpt.html").read_text()


async def test_probe_parses_assistant_message_and_citations():
    cap = FakeCaptureClient(
        {"chatgpt": CapturePage(html=HTML, final_url="https://chatgpt.com/c/abc")}
    )
    a = ChatGPTAdapter(capture=cap)
    assert isinstance(a, EngineAdapter)
    r = await a.probe("best crm for smb?", persona="smb_buyer")
    assert isinstance(r, ProbeResult)
    assert r.engine == "chatgpt"
    assert r.answer_text
    assert r.cited_urls and all(u.startswith("http") for u in r.cited_urls)


async def test_probe_normalizes_and_dedupes_citations_in_document_order():
    cap = FakeCaptureClient(
        {"chatgpt": CapturePage(html=HTML, final_url="https://chatgpt.com/c/abc")}
    )
    a = ChatGPTAdapter(capture=cap)
    r = await a.probe("best crm for smb?")
    # The fixture repeats the HubSpot link (inline + footnote) and cites the Zoho link both
    # with and without a trailing slash -- both must collapse to a single normalized entry,
    # and a bare `#footnote-marker` / relative `/legal` / `mailto:` link must be dropped.
    assert r.cited_urls == [
        "https://www.hubspot.com/products/crm",
        "https://www.zoho.com/crm",
        "https://www.pipedrive.com/en/features",
    ]


@pytest.mark.parametrize(
    "garbled_html",
    [
        "",
        "<html><body><p>Something went wrong. Please try refreshing.</p></body></html>",
        "<<<>>>not html at all????",
    ],
    ids=["empty", "no-expected-nodes", "malformed-markup"],
)
async def test_probe_is_resilient_to_garbled_or_empty_html(garbled_html):
    cap = FakeCaptureClient(
        {"chatgpt": CapturePage(html=garbled_html, final_url="https://chatgpt.com/c/broken")}
    )
    a = ChatGPTAdapter(capture=cap)
    r = await a.probe("best crm for smb?")  # must not raise
    assert isinstance(r, ProbeResult)
    assert r.engine == "chatgpt"
    assert r.cited_urls == []
