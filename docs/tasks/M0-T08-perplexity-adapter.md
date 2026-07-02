# M0-T08 — Perplexity Sonar adapter

**Depends on:** T06 · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** First real engine adapter. Perplexity Sonar returns citations via API → easiest,
highest-signal starting point. HTTP mocked in tests (`respx`).

**Files:**
- Create: `src/gw_geo/measurement/probe/perplexity.py`
- Test: `tests/measurement/probe/test_perplexity.py`, `tests/fixtures/answers/perplexity_api.json`

## Interface

```python
class PerplexityAdapter:
    name = "perplexity"; supports_citations = True
    def __init__(self, api_key: str, client: "httpx.AsyncClient | None" = None,
                 model: str = "sonar") -> None: ...
    async def probe(self, prompt: str, *, geo: str = "us",
                    persona: str | None = None) -> ProbeResult: ...
```

Endpoint: `POST https://api.perplexity.ai/chat/completions`, bearer auth. Map response:
`choices[0].message.content → answer_text`; `citations[] → cited_urls`; measure `latency_ms`;
compute `cost_usd` from token usage (constant rate table in the module).

## Steps
- [ ] **1. Failing test** `tests/measurement/probe/test_perplexity.py` (mock with `respx`):

```python
import httpx, respx, pytest
from gw_geo.measurement.probe.perplexity import PerplexityAdapter

@respx.mock
async def test_probe_maps_citations():
    respx.post("https://api.perplexity.ai/chat/completions").mock(return_value=httpx.Response(
        200, json={"choices":[{"message":{"content":"Foo is great"}}],
                    "citations":["https://a.com","https://b.com"],
                    "usage":{"prompt_tokens":10,"completion_tokens":20}}))
    a = PerplexityAdapter(api_key="k", client=httpx.AsyncClient())
    r = await a.probe("best crm?")
    assert r.engine == "perplexity"
    assert r.cited_urls == ["https://a.com","https://b.com"]
    assert r.cost_usd > 0
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** the adapter; call `base.register(PerplexityAdapter(...))` at wiring time
  (in the runner/CLI, not at import — keep import side-effect-free).
- [ ] **4. Run → pass**; mypy clean; confirm `isinstance(adapter, EngineAdapter)`.
- [ ] **5. Commit:** `feat(measurement): perplexity sonar adapter`

## Acceptance
- Conforms to `EngineAdapter`; maps content + citations + cost; no live network in tests; passes
  the T10 contract suite.
