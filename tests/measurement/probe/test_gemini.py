"""Spec test for the Gemini adapter (docs/tasks/M1-T03-gemini-adapter.md).

Reformatted from the task spec's compact, comma-joined import style (`import httpx, respx, json,
pathlib, re`) into ruff-clean form: one import per line, and `re` dropped since the spec body
never actually uses it (respx's `url__regex` lookup takes a plain string pattern). Every
assertion below is identical to the spec, plus an `isinstance(adapter, EngineAdapter)` check --
the shared T10 contract-suite registration for Gemini is consolidated separately in T19, so
per-adapter conformance is proven here instead.
"""

import json
import pathlib

import httpx
import respx

from gw_geo.measurement.probe.base import EngineAdapter
from gw_geo.measurement.probe.gemini import GeminiAdapter

FIX = json.loads(pathlib.Path("tests/fixtures/answers/gemini_api.json").read_text())


@respx.mock
async def test_probe_maps_grounding_citations():
    respx.route(
        method="POST",
        url__regex=r"https://generativelanguage\.googleapis\.com/.*:generateContent",
    ).mock(return_value=httpx.Response(200, json=FIX))
    a = GeminiAdapter(api_key="k", client=httpx.AsyncClient())
    assert isinstance(a, EngineAdapter)
    r = await a.probe("best crm for smb?")
    assert r.engine == "gemini"
    assert r.answer_text
    assert r.cited_urls and all(u.startswith("http") for u in r.cited_urls)
    assert r.cost_usd > 0
