# M4-T12 — Retrain trigger (drift breach → ranking-model retrain)

**Depends on:** T02 (`retrain_job`), M1 `drift_event` (`orchestration/drift.py`) · **Wave:** 2
**Suggested agent:** general-purpose

**Goal:** Extend the M1 drift canary into self-adaptation: a breached `drift_event` with
`retrain_flag=True` triggers a **retrain job** for the affected engine's ranking model (design §3.1,
PRD §6.6). The trainer is an **injected `Retrainer` protocol** (satisfied by M3 `ranking/`) so CI
never trains for real or pulls live data. Idempotent: exactly one `retrain_job` per `drift_event`.

**Files:**
- Create: `src/gw_geo/orchestration/retrain.py`
- Test: `tests/orchestration/test_retrain.py`

## Interface (design §3.1)

```python
from typing import Any, Literal, Protocol
from pydantic import BaseModel

class RetrainJob(BaseModel):
    id: str; model_engine: str; trigger_drift_event_id: str
    status: Literal["pending", "running", "succeeded", "failed"]
    metrics_before: dict[str, float]; metrics_after: dict[str, float]
    model_ref: str | None = None

class Retrainer(Protocol):                       # satisfied by M3 ranking trainer
    def retrain(self, *, engine: str) -> dict[str, Any]: ...   # {"model_ref":..., "metrics":{...}}

class RetrainTrigger:
    def __init__(self, session, *, retrainer: Retrainer) -> None: ...
    def poll(self) -> list[RetrainJob]: ...      # scan unhandled breached+flagged drift_events
    def on_breach(self, drift_event_id: str) -> RetrainJob: ...   # idempotent per event
```

`on_breach` creates (or returns the existing) `retrain_job` for the event, calls `retrainer.retrain`,
records `model_ref`/`metrics_after`, sets `status="succeeded"`, and clears the event's `retrain_flag`.
`poll` runs `on_breach` for every `drift_event` where `breached and retrain_flag` and no job exists yet.

## Steps
- [ ] **1. Failing test** `tests/orchestration/test_retrain.py` (SQLite; assumes M1 `drift_event`
  table/model — create a minimal row directly):

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from gw_geo.common.db import Base, DriftEvent, RetrainJob as RetrainJobRow
from gw_geo.orchestration.retrain import RetrainTrigger

class FakeRetrainer:
    def __init__(self): self.calls = 0
    def retrain(self, *, engine):
        self.calls += 1
        return {"model_ref": f"s3://models/{engine}/v2", "metrics": {"auc": 0.81}}

def _session_with_breach():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); s = Session(eng)
    s.add(DriftEvent(id="d1", engine="perplexity", canary_id="c1", baseline_rate=0.6,
        observed_rate=0.3, drop=0.3, breached=True, retrain_flag=True)); s.commit()
    return s

def test_on_breach_creates_job_and_clears_flag():
    s = _session_with_breach(); r = FakeRetrainer()
    job = RetrainTrigger(s, retrainer=r).on_breach("d1")
    assert job.status == "succeeded" and job.model_ref.endswith("v2")
    assert job.metrics_after["auc"] == 0.81 and r.calls == 1
    assert s.get(DriftEvent, "d1").retrain_flag is False

def test_on_breach_is_idempotent():
    s = _session_with_breach(); r = FakeRetrainer()
    trig = RetrainTrigger(s, retrainer=r)
    j1 = trig.on_breach("d1"); j2 = trig.on_breach("d1")
    assert j1.id == j2.id and r.calls == 1
    assert s.query(RetrainJobRow).count() == 1

def test_poll_handles_all_flagged_breaches():
    s = _session_with_breach()
    jobs = RetrainTrigger(s, retrainer=FakeRetrainer()).poll()
    assert len(jobs) == 1 and jobs[0].model_engine == "perplexity"
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `RetrainTrigger`: idempotent job creation keyed on `trigger_drift_event_id`,
  injected `retrainer` call, flag-clear on success, `poll` over flagged breaches. No live training.
- [ ] **4. Run → pass**; add a failure-path test (retrainer raises → `status="failed"`, flag NOT cleared).
- [ ] **5. Commit:** `feat(orchestration): drift-breach retrain trigger`

## Acceptance
- A breached+flagged `drift_event` yields exactly one `retrain_job` (idempotent), calls the injected
  `Retrainer`, records metrics/model_ref, and clears the flag on success (keeps it on failure);
  hermetic (no real training).
