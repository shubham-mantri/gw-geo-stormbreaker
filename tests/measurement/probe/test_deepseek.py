"""Spec test for the DeepSeek adapter (docs/tasks/M1-T06-deepseek-adapter.md).

DeepSeek chat returns no first-class citations (`supports_citations = False`), so `cited_urls`
is always empty here -- the shared T10 contract suite (wired up separately, see T19) permits an
empty `cited_urls` list for adapters that declare `supports_citations = False`. The adapter is
config-toggled off by default at the `build_runtime` registration layer (`deepseek_enabled`, TRD
OT3, wired in T18) -- unrelated to this module, which is always implemented and tested.

Reformatted from the task spec's compact, comma-joined import style into ruff-clean form (one
import per line, standard blank-line spacing); assertions extend the spec with the
`EngineAdapter` conformance check called out in this task's environment brief.
"""

import json
import pathlib

import httpx
import respx

from gw_geo.measurement.probe.base import EngineAdapter
from gw_geo.measurement.probe.deepseek import DeepSeekAdapter

FIX = json.loads(pathlib.Path("tests/fixtures/answers/deepseek_api.json").read_text())


@respx.mock
async def test_probe_maps_content_no_citations():
    respx.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(200, json=FIX)
    )
    a = DeepSeekAdapter(api_key="k", client=httpx.AsyncClient())
    assert isinstance(a, EngineAdapter)

    r = await a.probe("best crm for smb?")

    assert r.engine == "deepseek"
    assert r.answer_text
    assert a.supports_citations is False
    assert all(u.startswith("http") for u in r.cited_urls)
    assert r.cost_usd > 0
