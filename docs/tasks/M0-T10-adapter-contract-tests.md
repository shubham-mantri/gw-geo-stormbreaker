# M0-T10 — Adapter contract test suite

**Depends on:** T06 (validates T08, T09) · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** One shared test suite that every engine adapter must pass, so new adapters can't drift
from the contract (TRD §12). Parametrized over all registered adapters using recorded fixtures.

**Files:**
- Create: `tests/measurement/probe/test_adapter_contract.py`
- Create: `tests/measurement/probe/fixtures.py` (fixture-backed fake transport per adapter)

## What it asserts (for each adapter)
- `isinstance(adapter, EngineAdapter)` is True.
- `adapter.name` is a non-empty unique string; `supports_citations` is a bool.
- `await adapter.probe("test prompt")` returns a `ProbeResult` with:
  - `engine == adapter.name`
  - `answer_text` non-empty
  - `cited_urls` is a list of `http(s)` URLs (may be empty only if `supports_citations is False`)
  - `latency_ms >= 0`, `cost_usd >= 0`

## Steps
- [ ] **1. Write the parametrized suite:**

```python
import pytest, httpx, respx
from gw_geo.common.models import ProbeResult
from gw_geo.measurement.probe.base import EngineAdapter
from gw_geo.measurement.probe.perplexity import PerplexityAdapter
from gw_geo.measurement.probe.openai_chatgpt import OpenAIAdapter
from tests.measurement.probe.fixtures import mock_for  # sets up respx routes per adapter

CASES = [
    ("perplexity", lambda: PerplexityAdapter(api_key="k", client=httpx.AsyncClient())),
    ("openai",     lambda: OpenAIAdapter(api_key="k", client=httpx.AsyncClient())),
]

@pytest.mark.parametrize("name,factory", CASES)
@respx.mock
async def test_adapter_contract(name, factory):
    mock_for(name)
    a = factory()
    assert isinstance(a, EngineAdapter)
    r = await a.probe("best crm for smb?")
    assert isinstance(r, ProbeResult)
    assert r.engine == a.name and r.answer_text
    assert all(u.startswith("http") for u in r.cited_urls)
    assert r.latency_ms >= 0 and r.cost_usd >= 0
```

- [ ] **2. Implement `fixtures.py`** — `mock_for(name)` registers the right `respx` route +
  fixture JSON for each engine. New adapters add one `CASES` entry + a `mock_for` branch.
- [ ] **3. Run → pass** for both adapters.
- [ ] **4. Commit:** `test(measurement): shared engine-adapter contract suite`

## Acceptance
- Both M0 adapters pass the shared suite; adding an adapter requires only a `CASES` row + a
  `mock_for` branch (documented in the file header).
