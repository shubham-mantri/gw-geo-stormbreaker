# M4-T15 — Continuous-loop scheduler (adaptation cycle)

**Depends on:** T05 (discovery), T10 (workflow), T12 (retrain), T14 (effort) · **Wave:** 3
**Suggested agent:** general-purpose (integration task — assign after Wave 2 merges)

**Goal:** Orchestrate one **adaptation cycle** — measure → sense → adapt (design §3.3, PRD §6.6):
run the drift canary, fire retrain triggers on breaches, discover seeding targets, bandit-allocate
effort, spawn `seeding_task`s (status `todo`) for humans, and collect alerts. Pure orchestration:
**every collaborator is injected**, so it's unit-tested with fakes and does no live work.

**Files:**
- Create: `src/gw_geo/orchestration/scheduler.py`
- Test: `tests/orchestration/test_scheduler.py`

## Interface (design §3.3)

```python
from pydantic import BaseModel, Field

class CycleResult(BaseModel):
    drift_breaches: int = 0
    retrain_jobs: list[str] = Field(default_factory=list)
    targets_found: int = 0
    tasks_spawned: int = 0
    alerts: list[str] = Field(default_factory=list)

def run_adaptation_cycle(session, *, tenant_id: str, brand_id: str, since: str, until: str,
                         drift_runner, retrain_trigger, discovery, workflow,
                         bandit_policy, budget: int, date: str) -> CycleResult: ...
```

Sequence: (1) `drift_runner()` → list of `DriftResult`; count breaches; (2) `retrain_trigger.poll()`
→ collect job ids + a `"🔴 retrain"` alert per job; (3) `discovery()` → `SeedingTarget`s; (4)
allocate `budget` across the targets' channels via `bandit_policy` (T14 `allocate_effort` may be
wrapped by the caller and passed in as `discovery`/effort closures — keep the collaborators injected);
(5) for each allocated slot, `workflow.create(...)` a `todo` task and increment `tasks_spawned`;
(6) emit a `"🎯 opportunity"` alert per new target. Return the `CycleResult`.

## Steps
- [ ] **1. Failing test** `tests/orchestration/test_scheduler.py` (all fakes, no DB writes needed
  beyond the workflow's own SQLite session):

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from gw_geo.common.db import Base, SeedingTask
from gw_geo.common.models import SourceType
from gw_geo.seeding.discovery import SeedingTarget
from gw_geo.orchestration.scheduler import run_adaptation_cycle

class FakeDriftRunner:
    def __call__(self):
        class D:  # minimal DriftResult-like
            breached = True
        return [D()]

class FakeRetrain:
    def poll(self):
        class J: id = "rj1"; model_engine = "perplexity"
        return [J()]

class FakeDiscovery:
    def __call__(self):
        return [SeedingTarget(channel="reddit", source_type=SourceType.REDDIT,
                domain="reddit.com", engine="perplexity", gap_score=0.6, priority=0.6,
                rationale="gap")]

class RecordingWorkflow:
    def __init__(self, session): self.session = session; self.created = []
    def create(self, *, brand_id, channel, target_url=None, content_asset_id=None):
        tid = f"st{len(self.created)}"; self.created.append((channel, tid)); return tid

def test_cycle_spawns_tasks_and_reports():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); s = Session(eng)
    res = run_adaptation_cycle(s, tenant_id="t1", brand_id="b1", since="a", until="b",
        drift_runner=FakeDriftRunner(), retrain_trigger=FakeRetrain(),
        discovery=FakeDiscovery(), workflow=RecordingWorkflow(s),
        bandit_policy=None, budget=3, date="2026-07-02")
    assert res.drift_breaches == 1
    assert res.retrain_jobs == ["rj1"]
    assert res.targets_found == 1 and res.tasks_spawned >= 1
    assert any("retrain" in a.lower() for a in res.alerts)
    assert any("reddit" in a.lower() or "opportunit" in a.lower() for a in res.alerts)
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `run_adaptation_cycle` per the sequence; keep collaborators injected (no direct
  imports of concrete drift/discovery). No live calls.
- [ ] **4. Run → pass**; add a "no breaches, no targets" test yielding an empty-ish `CycleResult`.
- [ ] **5. Commit:** `feat(orchestration): continuous adaptation-cycle scheduler`

## Acceptance
- One cycle counts breaches, collects retrain job ids, spawns `todo` seeding tasks for discovered
  targets, and emits alerts — all via injected collaborators; hermetic (no live measurement/training/posting).
