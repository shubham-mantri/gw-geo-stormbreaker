"""Spec tests for the capture seam (docs/tasks/M1-T07-capture-seam.md).

Reformatted from the task spec's compact literal/comment spacing into ruff-clean, multi-line
form -- every assertion below is identical to the spec.
"""

from gw_geo.capture.base import CapturePage, CaptureClient
from tests.capture.fakes import FakeCaptureClient


async def test_fake_capture_client_conforms_and_serves():
    pages = {
        "google_ai_overviews": CapturePage(
            html="<div>hi</div>", final_url="https://www.google.com/search?q=x"
        )
    }
    c = FakeCaptureClient(pages)
    assert isinstance(c, CaptureClient)  # runtime-checkable Protocol
    page = await c.fetch("best crm", surface="google_ai_overviews", geo="us", persona=None)
    assert page.html == "<div>hi</div>"
    assert page.final_url.startswith("https://")


def test_capture_page_defaults():
    p = CapturePage(html="<x/>", final_url="https://e.com")
    assert p.screenshots == []
    assert p.meta == {}
