# M1-T05 — Copilot / Bing adapter (Bing Copilot API)

**Depends on:** M0-T06 (engine adapter base) · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** API adapter for Microsoft Copilot (Bing) via `httpx`, extracting the grounded answer and
its source citations. HTTP mocked in tests (`respx`); one recorded fixture; add a T10
contract-suite entry.

**Files:**
- Create: `src/gw_geo/measurement/probe/copilot.py`
- Test: `tests/measurement/probe/test_copilot.py`, `tests/fixtures/answers/copilot_api.json`
- Modify: `tests/measurement/probe/test_adapter_contract.py`, `tests/measurement/probe/fixtures.py`
  (add `("copilot", ...)` CASES row + `mock_for` branch)

## Interface

```python
class CopilotAdapter:
    name = "copilot"; supports_citations = True
    def __init__(self, api_key: str, client: "httpx.AsyncClient | None" = None,
                 model: str = "copilot") -> None: ...
    async def probe(self, prompt: str, *, geo: str = "us",
                    persona: str | None = None) -> ProbeResult: ...
```

Endpoint: the Bing/Copilot chat completion endpoint over `httpx` with the configured auth header
(`Authorization: Bearer` / `Ocp-Apim-Subscription-Key` per the Copilot API contract; keep the exact
header in a module constant so it is verifiable against current docs). Map response: grounded answer
message → `answer_text`; the attribution/source list (each source's `url`) → `cited_urls`; measure
`latency_ms`; compute `cost_usd` via a per-model rate table (or a flat per-request rate if the API
is not token-billed — document which in the module). `geo` maps to the Bing market/`mkt` param where
supported; document the mapping. Import side-effect-free; registered in `build_runtime` when
`copilot_api_key` is set (T18).

## Steps
- [ ] **1. Failing test** `tests/measurement/probe/test_copilot.py` (mock with `respx`, load fixture):

```python
import httpx, respx, json, pathlib
from gw_geo.measurement.probe.copilot import CopilotAdapter

FIX = json.loads(pathlib.Path("tests/fixtures/answers/copilot_api.json").read_text())

@respx.mock
async def test_probe_maps_answer_and_sources():
    respx.route(method="POST", host="api.bing.microsoft.com").mock(
        return_value=httpx.Response(200, json=FIX))
    a = CopilotAdapter(api_key="k", client=httpx.AsyncClient())
    r = await a.probe("best crm for smb?")
    assert r.engine == "copilot"
    assert r.answer_text
    assert r.cited_urls and all(u.startswith("http") for u in r.cited_urls)
    assert r.cost_usd >= 0
```

The fixture must be a realistic Copilot/Bing response carrying an answer message plus a list of
attributed source URLs.

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `copilot.py` per interface (inject `httpx.AsyncClient`, construct if `None`;
  auth header + endpoint host as module constants; rate table in module).
- [ ] **4. Run → pass**; mypy clean; confirm `isinstance(adapter, EngineAdapter)`.
- [ ] **5. Add T10 entry:** append `("copilot", lambda: CopilotAdapter(api_key="k", client=httpx.AsyncClient()))`
  to `CASES` and a `mock_for("copilot")` branch routing the Copilot endpoint to `copilot_api.json`.
  Run the contract suite → pass.
- [ ] **6. Commit:** `feat(measurement): copilot/bing adapter`

## Acceptance
- Conforms to `EngineAdapter`; maps answer + source citations + cost; endpoint/auth documented and
  verifiable against current docs; hermetic tests; passes the T10 contract suite.
