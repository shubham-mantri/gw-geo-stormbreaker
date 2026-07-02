# M1-T06 — DeepSeek adapter (toggle-gated)

**Depends on:** M0-T06 (engine adapter base) · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** API adapter for DeepSeek chat via `httpx`. **Config-toggled off by default** (`deepseek_enabled=False`,
TRD OT3) — the adapter exists and is contract-tested, but `build_runtime` only registers it when the
toggle is on. HTTP mocked in tests (`respx`); one recorded fixture; add a T10 contract-suite entry.

**Files:**
- Create: `src/gw_geo/measurement/probe/deepseek.py`
- Test: `tests/measurement/probe/test_deepseek.py`, `tests/fixtures/answers/deepseek_api.json`
- Modify: `tests/measurement/probe/test_adapter_contract.py`, `tests/measurement/probe/fixtures.py`
  (add `("deepseek", ...)` CASES row + `mock_for` branch)

## Interface

```python
class DeepSeekAdapter:
    name = "deepseek"; supports_citations = False   # DeepSeek chat returns no first-class citations
    def __init__(self, api_key: str, client: "httpx.AsyncClient | None" = None,
                 model: str = "deepseek-chat") -> None: ...
    async def probe(self, prompt: str, *, geo: str = "us",
                    persona: str | None = None) -> ProbeResult: ...
```

Endpoint: `POST https://api.deepseek.com/chat/completions` (OpenAI-compatible schema), bearer auth.
Map response: `choices[0].message.content → answer_text`; `cited_urls = []` (no first-class
citations — `supports_citations = False`, so the contract suite permits an empty list); measure
`latency_ms`; compute `cost_usd` from `usage` token counts via a per-model rate table. Import
side-effect-free; registered in `build_runtime` **only when `deepseek_enabled` is True and
`deepseek_api_key` is set** (T18).

## Steps
- [ ] **1. Failing test** `tests/measurement/probe/test_deepseek.py` (mock with `respx`, load fixture):

```python
import httpx, respx, json, pathlib
from gw_geo.measurement.probe.deepseek import DeepSeekAdapter

FIX = json.loads(pathlib.Path("tests/fixtures/answers/deepseek_api.json").read_text())

@respx.mock
async def test_probe_maps_content_no_citations():
    respx.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(200, json=FIX))
    a = DeepSeekAdapter(api_key="k", client=httpx.AsyncClient())
    r = await a.probe("best crm for smb?")
    assert r.engine == "deepseek"
    assert r.answer_text
    assert r.supports_citations is False and r.cited_urls == []
    assert r.cost_usd > 0
```

The fixture must be a realistic OpenAI-compatible chat completion with `choices[0].message.content`
and a `usage` object.

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `deepseek.py` per interface (inject `httpx.AsyncClient`, construct if `None`;
  rate table in module).
- [ ] **4. Run → pass**; mypy clean; confirm `isinstance(adapter, EngineAdapter)`.
- [ ] **5. Add T10 entry:** append `("deepseek", lambda: DeepSeekAdapter(api_key="k", client=httpx.AsyncClient()))`
  to `CASES` and a `mock_for("deepseek")` branch routing `POST /chat/completions` to
  `deepseek_api.json`. Run the contract suite → pass. (The adapter is always contract-tested; only
  its runtime *registration* is toggle-gated.)
- [ ] **6. Commit:** `feat(measurement): deepseek adapter (toggle-gated)`

## Acceptance
- Conforms to `EngineAdapter`; maps content + cost; `supports_citations = False` with empty
  `cited_urls` accepted by the contract suite; hermetic tests; passes the T10 contract suite;
  registration gated on `deepseek_enabled` (verified in T18).
