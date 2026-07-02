"""Spec test for the Grok adapter (docs/tasks/M1-T13-grok-adapter.md).

Every assertion in the task spec's `test_probe_parses_grok_answer_and_sources` is kept verbatim,
plus a full `ProbeResult` type check. Two extra tests cover the m1-design.md §10 resilience
requirement (consumer DOM is unstable): garbled markup and an empty capture must never raise and
must degrade to `cited_urls == []`.

T10's shared contract suite (`test_adapter_contract.py`) and its `fixtures.py` mock registry are
intentionally left untouched here: the task's step 5 (add a `("grok", ...)` CASES row + `mock_for`
branch) is consolidated into T19 to avoid parallel-edit conflicts across the M1 adapter tasks.
"""

import pathlib

from gw_geo.capture.base import CapturePage
from gw_geo.common.models import ProbeResult
from gw_geo.measurement.probe.base import EngineAdapter
from gw_geo.measurement.probe.grok import GrokAdapter
from tests.capture.fakes import FakeCaptureClient

HTML = pathlib.Path("tests/fixtures/answers/grok.html").read_text()


async def test_probe_parses_grok_answer_and_sources():
    cap = FakeCaptureClient(
        {"grok": CapturePage(html=HTML, final_url="https://grok.com/chat/abc")}
    )
    a = GrokAdapter(capture=cap)
    assert isinstance(a, EngineAdapter)
    r = await a.probe("best crm for smb?")
    assert isinstance(r, ProbeResult)
    assert r.engine == "grok"
    assert r.answer_text
    assert r.cited_urls and all(u.startswith("http") for u in r.cited_urls)


async def test_probe_scopes_citations_and_dedupes_normalized_urls():
    cap = FakeCaptureClient(
        {"grok": CapturePage(html=HTML, final_url="https://grok.com/chat/abc")}
    )
    a = GrokAdapter(capture=cap)
    r = await a.probe("best crm for smb?")
    # the footer link lives outside the citations container and must not leak in; the
    # trailing-slash duplicate of the g2.com link must collapse into a single entry.
    assert r.cited_urls == [
        "https://www.g2.com/categories/crm",
        "https://www.softwareadvice.com/crm",
        "https://www.capterra.com/crm-software",
    ]


async def test_probe_is_resilient_to_garbled_html():
    cap = FakeCaptureClient(
        {
            "grok": CapturePage(
                html="<div><span>not really grok<div", final_url="https://grok.com/chat/broken"
            )
        }
    )
    a = GrokAdapter(capture=cap)
    r = await a.probe("best crm for smb?")
    assert r.engine == "grok"
    assert r.cited_urls == []


async def test_probe_is_resilient_to_empty_html():
    cap = FakeCaptureClient(
        {"grok": CapturePage(html="", final_url="https://grok.com/chat/empty")}
    )
    a = GrokAdapter(capture=cap)
    r = await a.probe("best crm for smb?")
    assert r.engine == "grok"
    assert r.answer_text == ""
    assert r.cited_urls == []
