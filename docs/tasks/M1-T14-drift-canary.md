# M1-T14 — Drift canary (`orchestration/drift.py`)

**Depends on:** T02 (drift_event table), M0-T12 (aggregate) · **Wave:** 2
**Suggested agent:** general-purpose

**Goal:** Detect when an engine's behavior shifts. Run a fixed canary set through the normal
adapters, compare observed mention/citation rates to known-good baselines, and on breach write a
`drift_event` row + emit an alert + set a retrain flag (m1-design §4, TRD §5.6).

**Files:**
- Create: `src/gw_geo/orchestration/drift.py`
- Test: `tests/orchestration/__init__.py`, `tests/orchestration/test_drift.py`

## Interface

```python
from pydantic import BaseModel

class DriftResult(BaseModel):
    engine: str; canary_id: str
    baseline_rate: float; observed_rate: float; drop: float
    breached: bool

class Canary(BaseModel):
    canary_id: str; engine: str; prompt: str; brand: str
    baseline_rate: float                       # known-good mention/citation rate

def load_canaries(session=None) -> list[Canary]: ...     # from config/seed

def run_drift_canary(session, *, engines: list[str], threshold: float = 0.2,
                     extractor, archive, date: str) -> list[DriftResult]: ...
```

Behavior: for each canary whose `engine` is in `engines`, probe via `probe.base.get_adapter` (each
probed enough times to estimate a rate), parse+aggregate to an `observed_rate`; compute
`drop = baseline_rate - observed_rate`; `breached = drop > threshold`. On breach: write a
`DriftEvent` row (with `retrain_flag=True`) and emit an alert via an injected alert hook (structured
log locally; SNS in deploy — T17 wires the real one). Return one `DriftResult` per canary.

## Steps
- [ ] **1. Failing test** `tests/orchestration/test_drift.py` (register a fake adapter, SQLite session):

```python
from gw_geo.common.models import ProbeResult
from gw_geo.common.db import Base, DriftEvent
from gw_geo.measurement.probe import base
from gw_geo.orchestration import drift
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

class DriftyAdapter:
    name = "gemini"; supports_citations = True
    async def probe(self, prompt, *, geo="us", persona=None):
        return ProbeResult(engine="gemini", answer_text="no brands here", cited_urls=[])

class StubExtractor:
    def extract(self, text, brand):
        return {"brand_mentioned": False, "position": None, "sentiment": "neutral",
                "competitors_present": []}

class MemArchive:
    def put(self, key, payload): return key

def _session():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); return Session(eng)

async def test_breach_writes_event_and_flags_retrain(monkeypatch):
    base.clear_registry(); base.register(DriftyAdapter())
    monkeypatch.setattr(drift, "load_canaries", lambda session=None: [
        drift.Canary(canary_id="c1", engine="gemini", prompt="best crm?",
                     brand="Foo", baseline_rate=0.9)])
    s = _session()
    results = await drift.run_drift_canary(s, engines=["gemini"], threshold=0.2,
        extractor=StubExtractor(), archive=MemArchive(), date="2026-07-02")
    assert results[0].breached is True and results[0].drop > 0.2
    rows = s.execute(select(DriftEvent)).scalars().all()
    assert len(rows) == 1 and rows[0].retrain_flag is True and rows[0].engine == "gemini"
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `drift.py` (`load_canaries` from a config/seed constant for now; `run_drift_canary`
  probes→aggregates→compares→writes `DriftEvent` on breach; alert via an injected/optional hook,
  defaulting to a structured log). Make `run_drift_canary` `async`.
- [ ] **4. Run → pass**; add a non-breach test (observed ≥ baseline) asserting **no** `DriftEvent` row
  is written and `breached is False`.
- [ ] **5. Commit:** `feat(orchestration): drift canary with drift_event write + alert hook`

## Acceptance
- Compares observed vs baseline per canary; `drop > threshold` → breach; breach writes a
  `drift_event` row with `retrain_flag=True` and emits an alert; non-breach writes nothing; hermetic
  (fake adapter/extractor/archive, SQLite).
