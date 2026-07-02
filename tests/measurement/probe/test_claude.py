"""Spec test for the Claude adapter (docs/tasks/M1-T04-claude-adapter.md).

Reformatted from the task spec's compact, comma-joined import style into ruff-clean form (one
import per line, standard blank-line spacing) -- every assertion from the spec is kept verbatim,
plus an `isinstance(a, EngineAdapter)` check and a full `ProbeResult` type check. T10's shared
contract suite (`test_adapter_contract.py`) and its `fixtures.py` mock registry are intentionally
left untouched here: the task's step 5 (add a `("claude", ...)` CASES row + `mock_for` branch) is
consolidated into T19 to avoid parallel-edit conflicts across the M1 adapter tasks.
"""

import json
import pathlib

import httpx
import respx

from gw_geo.common.models import ProbeResult
from gw_geo.measurement.probe.base import EngineAdapter
from gw_geo.measurement.probe.claude import ClaudeAdapter

FIX = json.loads(pathlib.Path("tests/fixtures/answers/claude_api.json").read_text())


@respx.mock
async def test_probe_extracts_web_search_citations():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json=FIX)
    )
    a = ClaudeAdapter(api_key="k", client=httpx.AsyncClient())
    assert isinstance(a, EngineAdapter)
    r = await a.probe("best crm for smb?")
    assert isinstance(r, ProbeResult)
    assert r.engine == "claude"
    assert r.answer_text
    assert r.cited_urls and all(u.startswith("http") for u in r.cited_urls)
    assert r.cost_usd > 0
