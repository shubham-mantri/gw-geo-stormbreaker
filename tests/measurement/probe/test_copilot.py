"""Spec test for the Copilot/Bing adapter (docs/tasks/M1-T05-copilot-adapter.md).

Reformatted from the task spec's compact, comma-joined `import httpx, respx, json, pathlib`
(ruff E401) into ruff-clean form (one import per line, standard blank-line spacing) -- the
spec's own assertions are kept verbatim. This task's step 5 (T10 contract-suite `CASES` row +
`mock_for` branch in `tests/measurement/probe/fixtures.py`) is consolidated into T19, so this
file additionally asserts `EngineAdapter` conformance directly, per the orchestrator brief.
"""

import json
import pathlib

import httpx
import respx

from gw_geo.measurement.probe.base import EngineAdapter
from gw_geo.measurement.probe.copilot import CopilotAdapter

FIX = json.loads(pathlib.Path("tests/fixtures/answers/copilot_api.json").read_text())


@respx.mock
async def test_probe_maps_answer_and_sources():
    respx.route(method="POST", host="api.bing.microsoft.com").mock(
        return_value=httpx.Response(200, json=FIX)
    )
    a = CopilotAdapter(api_key="k", client=httpx.AsyncClient())
    assert isinstance(a, EngineAdapter)
    r = await a.probe("best crm for smb?")
    assert r.engine == "copilot"
    assert r.answer_text
    assert r.cited_urls and all(u.startswith("http") for u in r.cited_urls)
    assert r.cost_usd > 0
