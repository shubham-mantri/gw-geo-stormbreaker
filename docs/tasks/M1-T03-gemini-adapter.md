# M1-T03 — Gemini adapter (Google Generative Language API)

**Depends on:** M0-T06 (engine adapter base) · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** API adapter for Google Gemini via the Generative Language API over `httpx`, extracting
citations from grounding metadata. HTTP mocked in tests (`respx`); one recorded fixture; add a T10
contract-suite entry.

**Files:**
- Create: `src/gw_geo/measurement/probe/gemini.py`
- Test: `tests/measurement/probe/test_gemini.py`, `tests/fixtures/answers/gemini_api.json`
- Modify: `tests/measurement/probe/test_adapter_contract.py`, `tests/measurement/probe/fixtures.py`
  (add `("gemini", ...)` CASES row + `mock_for` branch)

## Interface

```python
class GeminiAdapter:
    name = "gemini"; supports_citations = True
    def __init__(self, api_key: str, client: "httpx.AsyncClient | None" = None,
                 model: str = "gemini-2.5-flash") -> None: ...
    async def probe(self, prompt: str, *, geo: str = "us",
                    persona: str | None = None) -> ProbeResult: ...
```

Endpoint: `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`
(request enables the `google_search` grounding tool). Map response:
`candidates[0].content.parts[*].text → answer_text`; grounding citations from
`candidates[0].groundingMetadata.groundingChunks[*].web.uri → cited_urls`; measure `latency_ms`;
compute `cost_usd` from `usageMetadata` token counts via a per-model rate table in the module.
Import side-effect-free; registered in `build_runtime` when `gemini_api_key` is set (T18).

## Steps
- [ ] **1. Failing test** `tests/measurement/probe/test_gemini.py` (mock with `respx`, load fixture):

```python
import httpx, respx, json, pathlib, re
from gw_geo.measurement.probe.gemini import GeminiAdapter

FIX = json.loads(pathlib.Path("tests/fixtures/answers/gemini_api.json").read_text())

@respx.mock
async def test_probe_maps_grounding_citations():
    respx.route(method="POST",
        url__regex=r"https://generativelanguage\.googleapis\.com/.*:generateContent").mock(
        return_value=httpx.Response(200, json=FIX))
    a = GeminiAdapter(api_key="k", client=httpx.AsyncClient())
    r = await a.probe("best crm for smb?")
    assert r.engine == "gemini"
    assert r.answer_text
    assert r.cited_urls and all(u.startswith("http") for u in r.cited_urls)
    assert r.cost_usd > 0
```

The fixture must be a realistic `generateContent` payload with `candidates[].content.parts[].text`,
a `groundingMetadata.groundingChunks[].web.uri` list, and `usageMetadata` token counts.

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `gemini.py` per interface (inject `httpx.AsyncClient`, construct if `None`;
  API key via `x-goog-api-key` header or `?key=`; rate table constant in module).
- [ ] **4. Run → pass**; mypy clean; confirm `isinstance(adapter, EngineAdapter)`.
- [ ] **5. Add T10 entry:** append `("gemini", lambda: GeminiAdapter(api_key="k", client=httpx.AsyncClient()))`
  to `CASES` and a `mock_for("gemini")` branch (routes the `generateContent` regex to
  `gemini_api.json`). Run the contract suite → pass.
- [ ] **6. Commit:** `feat(measurement): gemini adapter`

## Acceptance
- Conforms to `EngineAdapter`; maps text + grounding citations + cost; hermetic tests (no live
  network); passes the T10 contract suite.
