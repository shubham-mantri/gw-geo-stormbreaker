"""Shared engine-adapter contract suite (docs/tasks/M0-T10-adapter-contract-tests.md, TRD §12).

Every `EngineAdapter` must pass this one parametrized suite so adapters can't drift from the
contract in `measurement/probe/base.py`. To add a new adapter: add one `CASES` row here and one
`mock_for` branch in `fixtures.py` -- no other changes needed.

Reformatted from the task spec's comma-joined `import pytest, httpx, respx` (ruff E401) into
ruff-clean, one-import-per-line form; every assertion below is identical to the spec.
"""

import pathlib

import httpx
import pytest
import respx

from gw_geo.capture.base import CapturePage
from gw_geo.common.models import ProbeResult
from gw_geo.measurement.probe.ai_overviews import AIOverviewsAdapter
from gw_geo.measurement.probe.base import EngineAdapter
from gw_geo.measurement.probe.chatgpt import ChatGPTAdapter
from gw_geo.measurement.probe.claude import ClaudeAdapter
from gw_geo.measurement.probe.copilot import CopilotAdapter
from gw_geo.measurement.probe.deepseek import DeepSeekAdapter
from gw_geo.measurement.probe.gemini import GeminiAdapter
from gw_geo.measurement.probe.grok import GrokAdapter
from gw_geo.measurement.probe.openai_chatgpt import OpenAIAdapter
from gw_geo.measurement.probe.perplexity import PerplexityAdapter
from tests.capture.fakes import FakeCaptureClient
from tests.measurement.probe.fixtures import mock_for

# Recorded consumer-surface HTML for the Playwright adapters, served by a FakeCaptureClient so the
# contract suite exercises the real DOM parsers without ever launching a browser (TRD §12).
_FIXTURES_DIR = pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "answers"
_AI_OVERVIEWS_HTML = (_FIXTURES_DIR / "google_ai_overviews.html").read_text()
_CHATGPT_HTML = (_FIXTURES_DIR / "chatgpt.html").read_text()
_GROK_HTML = (_FIXTURES_DIR / "grok.html").read_text()


def _capture(surface: str, html: str, final_url: str) -> FakeCaptureClient:
    """Build a FakeCaptureClient that serves `html` for `surface` (Playwright adapter seam)."""
    return FakeCaptureClient({surface: CapturePage(html=html, final_url=final_url)})


# One (name, factory) row per engine. API adapters take an httpx client (their endpoint is routed
# to a recorded fixture by `mock_for`); Playwright adapters take a FakeCaptureClient serving a
# recorded HTML fixture (their `mock_for` branch is a no-op -- no HTTP call is made). Adding a new
# engine is one row here + one `mock_for` branch, nothing else (TRD §5.2, m1-design.md §1).
CASES = [
    # API adapters (M0 + M1-T03..T06).
    ("perplexity", lambda: PerplexityAdapter(api_key="k", client=httpx.AsyncClient())),
    ("openai", lambda: OpenAIAdapter(api_key="k", client=httpx.AsyncClient())),
    ("gemini", lambda: GeminiAdapter(api_key="k", client=httpx.AsyncClient())),
    ("claude", lambda: ClaudeAdapter(api_key="k", client=httpx.AsyncClient())),
    ("copilot", lambda: CopilotAdapter(api_key="k", client=httpx.AsyncClient())),
    ("deepseek", lambda: DeepSeekAdapter(api_key="k", client=httpx.AsyncClient())),
    # Playwright consumer-surface adapters (M1-T11..T13).
    (
        "google_ai_overviews",
        lambda: AIOverviewsAdapter(
            capture=_capture(
                "google_ai_overviews",
                _AI_OVERVIEWS_HTML,
                "https://www.google.com/search?q=best+crm",
            )
        ),
    ),
    (
        "chatgpt",
        lambda: ChatGPTAdapter(
            capture=_capture("chatgpt", _CHATGPT_HTML, "https://chatgpt.com/c/abc")
        ),
    ),
    (
        "grok",
        lambda: GrokAdapter(capture=_capture("grok", _GROK_HTML, "https://grok.com/chat/abc")),
    ),
]


@pytest.mark.parametrize("name,factory", CASES)
@respx.mock
async def test_adapter_contract(name, factory):
    mock_for(name)
    a = factory()
    assert isinstance(a, EngineAdapter)
    r = await a.probe("best crm for smb?")
    assert isinstance(r, ProbeResult)
    assert r.engine == a.name and r.answer_text
    assert all(u.startswith("http") for u in r.cited_urls)
    if a.supports_citations:
        assert r.cited_urls, f"{a.name} claims citations but returned none"
    assert r.latency_ms >= 0 and r.cost_usd >= 0
