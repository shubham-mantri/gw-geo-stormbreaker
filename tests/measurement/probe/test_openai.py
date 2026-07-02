"""Spec test for the OpenAI (ChatGPT) adapter (docs/tasks/M0-T09-openai-adapter.md).

Reformatted from the task spec's compact, comma-joined import style into ruff-clean form (one
import per line, standard blank-line spacing) -- every assertion below is identical to the spec.
"""

import json
import pathlib

import httpx
import respx

from gw_geo.measurement.probe.openai_chatgpt import OpenAIAdapter

FIX = json.loads(pathlib.Path("tests/fixtures/answers/openai_api.json").read_text())


@respx.mock
async def test_probe_extracts_text_and_citations():
    respx.post("https://api.openai.com/v1/responses").mock(
        return_value=httpx.Response(200, json=FIX)
    )
    a = OpenAIAdapter(api_key="k", client=httpx.AsyncClient())
    r = await a.probe("best crm for smb?")
    assert r.engine == "openai"
    assert r.answer_text
    assert all(u.startswith("http") for u in r.cited_urls)
