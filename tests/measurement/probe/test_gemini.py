"""Spec test for the Gemini adapter (docs/tasks/M1-T03-gemini-adapter.md).

Reformatted from the task spec's compact, comma-joined import style (`import httpx, respx, json,
pathlib, re`) into ruff-clean form: one import per line, and `re` dropped since the spec body
never actually uses it (respx's `url__regex` lookup takes a plain string pattern). Every
assertion below is identical to the spec, plus an `isinstance(adapter, EngineAdapter)` check --
the shared T10 contract-suite registration for Gemini is consolidated separately in T19, so
per-adapter conformance is proven here instead.

Also covers M1 review fix F2: real Generative-Language grounding responses return an opaque Vertex
*redirect* in `groundingChunks[*].web.uri` and the true source domain in `web.title`, so the
adapter must resolve citations from the title (else every live Gemini citation collapses to
`vertexaisearch.cloud.google.com` -> `classify_source` == `other`).
"""

import json
import pathlib
from typing import Any

import httpx
import respx

from gw_geo.measurement.probe.base import EngineAdapter
from gw_geo.measurement.probe.gemini import GeminiAdapter

FIX = json.loads(pathlib.Path("tests/fixtures/answers/gemini_api.json").read_text())

_URL_RE = r"https://generativelanguage\.googleapis\.com/.*:generateContent"


async def _probe_with(payload: dict[str, Any]) -> Any:
    """Route Gemini's endpoint to `payload` and return the adapter's `ProbeResult`."""
    respx.route(method="POST", url__regex=_URL_RE).mock(
        return_value=httpx.Response(200, json=payload)
    )
    a = GeminiAdapter(api_key="k", client=httpx.AsyncClient())
    assert isinstance(a, EngineAdapter)
    return await a.probe("best crm for smb?")


@respx.mock
async def test_probe_maps_grounding_citations() -> None:
    r = await _probe_with(FIX)
    assert r.engine == "gemini"
    assert r.answer_text
    assert r.cited_urls and all(u.startswith("http") for u in r.cited_urls)
    assert r.cost_usd > 0
    # F2: the fixture's grounding chunks are Vertex redirects; citations must resolve to the real
    # source domains carried in `web.title`, never the opaque redirect host.
    assert r.cited_urls == ["https://g2.com", "https://capterra.com", "https://pcmag.com"]
    assert all("vertexaisearch.cloud.google.com" not in u for u in r.cited_urls)
    # The raw provider payload is retained verbatim on the ProbeResult.
    assert r.raw == FIX


@respx.mock
async def test_probe_resolves_redirects_keeps_direct_urls_and_dedupes() -> None:
    """A normal http(s) uri is kept as-is; a Vertex redirect resolves to its title's real source;
    two chunks that resolve to the same source are de-duplicated with first-seen order preserved."""
    payload = {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": "Some grounded answer."}]},
                "groundingMetadata": {
                    "groundingChunks": [
                        # Direct http(s) source URL -> kept verbatim.
                        {"web": {"uri": "https://www.nytimes.com/article", "title": "nytimes.com"}},
                        # Vertex redirect -> resolved from the bare-host title.
                        {
                            "web": {
                                "uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQabc123",
                                "title": "reddit.com",
                            }
                        },
                        # Different redirect token, same real source -> de-duped.
                        {
                            "web": {
                                "uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQxyz789",
                                "title": "reddit.com",
                            }
                        },
                    ]
                },
            }
        ],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 10, "totalTokenCount": 15},
        "modelVersion": "gemini-2.5-flash",
    }
    r = await _probe_with(payload)
    assert r.cited_urls == ["https://www.nytimes.com/article", "https://reddit.com"]
    assert all(u.startswith("http") for u in r.cited_urls)
