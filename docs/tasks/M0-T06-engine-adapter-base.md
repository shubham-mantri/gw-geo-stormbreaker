# M0-T06 — Engine adapter base + registry (KEYSTONE)

**Depends on:** T02 · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** The stable interface every engine implements, plus a registry. Adding an engine later =
one new adapter, zero core changes (TRD §5.2). Get this exactly right — T08/T09 and the runner
depend on it.

**Files:**
- Create: `src/gw_geo/measurement/probe/base.py`
- Test: `tests/measurement/probe/test_base.py`

## Interface

```python
from typing import Protocol, runtime_checkable
from gw_geo.common.models import ProbeResult

@runtime_checkable
class EngineAdapter(Protocol):
    name: str
    supports_citations: bool
    async def probe(self, prompt: str, *, geo: str = "us",
                    persona: str | None = None) -> ProbeResult: ...

_REGISTRY: dict[str, EngineAdapter] = {}
def register(adapter: EngineAdapter) -> None: ...      # keyed by adapter.name; dup name → ValueError
def get_adapter(name: str) -> EngineAdapter: ...       # unknown → KeyError
def all_adapters() -> list[EngineAdapter]: ...
def clear_registry() -> None: ...                       # test helper
```

## Steps
- [ ] **1. Failing test** `tests/measurement/probe/test_base.py`:

```python
import pytest
from gw_geo.common.models import ProbeResult
from gw_geo.measurement.probe import base

class FakeAdapter:
    name = "fake"; supports_citations = True
    async def probe(self, prompt, *, geo="us", persona=None):
        return ProbeResult(engine="fake", answer_text="hi", cited_urls=["https://x.com"])

def test_register_and_get():
    base.clear_registry(); a = FakeAdapter(); base.register(a)
    assert base.get_adapter("fake") is a
    assert isinstance(a, base.EngineAdapter)

def test_duplicate_name_rejected():
    base.clear_registry(); base.register(FakeAdapter())
    with pytest.raises(ValueError):
        base.register(FakeAdapter())

def test_unknown_adapter_raises():
    base.clear_registry()
    with pytest.raises(KeyError):
        base.get_adapter("nope")
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `base.py` per interface.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(measurement): engine adapter protocol + registry`

## Acceptance
- `EngineAdapter` is a runtime-checkable Protocol; registry registers/gets/lists/clears; duplicate
  name → `ValueError`; unknown → `KeyError`.
