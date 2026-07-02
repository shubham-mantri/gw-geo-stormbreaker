# M4-T10 — Seeding placement workflow (compliance-gated, human-in-the-loop)

**Depends on:** T02 (`seeding_task`), T03 (compliance engine), T04 (catalog) · **Wave:** 2
**Suggested agent:** general-purpose

**Goal:** The `seeding_task` state machine (design §2.5). Transition to `placed` is **compliance-gated
AND human-actioned** — the workflow persists the `ComplianceReport` and **hard-refuses** to mark a task
placed unless the stored report passed and the task is human-ready. There is **no auto-poster**: a
human supplies the `placed_url`. This is the enforcement point for PRD NG1 (with T03).

**Files:**
- Create: `src/gw_geo/seeding/workflow.py`
- Test: `tests/seeding/test_workflow.py`

## Interface (design §2.5)

```python
from enum import StrEnum
from gw_geo.seeding.compliance import ComplianceEngine, ComplianceReport, PlacementProposal, ComplianceError
from gw_geo.seeding.briefs import SeedingBrief

class SeedingStatus(StrEnum):
    TODO = "todo"; BRIEFED = "briefed"; COMPLIANCE_REVIEW = "compliance_review"
    READY_FOR_HUMAN = "ready_for_human"; PLACED = "placed"
    CORROBORATED = "corroborated"; REJECTED = "rejected"

class SeedingWorkflow:
    def __init__(self, session, tenant_id: str, engine: ComplianceEngine) -> None: ...
    def create(self, *, brand_id: str, channel: str, target_url: str | None = None,
               content_asset_id: str | None = None) -> str: ...          # → TODO, returns task_id
    def attach_brief(self, task_id: str, brief: SeedingBrief) -> None: ...  # TODO → BRIEFED
    def run_compliance(self, task_id: str, proposal: PlacementProposal) -> ComplianceReport: ...
    #   BRIEFED → COMPLIANCE_REVIEW → (READY_FOR_HUMAN if passed else REJECTED); persists report
    def mark_placed(self, task_id: str, *, placed_url: str, actor: str) -> None: ...
    #   RAISES ComplianceError unless status==READY_FOR_HUMAN and stored report.passed
```

All reads/writes go through the M0 `TenantScopedSession` semantics (tenant-scoped). `mark_placed`
re-reads the persisted `compliance_report` and asserts `passed is True` — the gate cannot be bypassed
by calling `mark_placed` directly.

## Steps
- [ ] **1. Failing test** `tests/seeding/test_workflow.py` (SQLite):

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from gw_geo.common.db import Base
from gw_geo.seeding.compliance import ComplianceEngine, PlacementProposal, ComplianceError
from gw_geo.seeding.workflow import SeedingWorkflow, SeedingStatus, SeedingTask  # SeedingTask re-exported or from db

def _wf():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng)
    s = Session(eng)
    return SeedingWorkflow(s, tenant_id="t1", engine=ComplianceEngine(ComplianceEngine.default_ruleset())), s

def test_clean_proposal_flows_to_placed():
    wf, s = _wf()
    tid = wf.create(brand_id="b1", channel="reddit")
    good = PlacementProposal(channel="reddit", body="Genuine comparison of CRMs.",
                             disclosure_text="Disclosure: I work at Acme.", author_is_real=True)
    rep = wf.run_compliance(tid, good)
    assert rep.passed is True
    wf.mark_placed(tid, placed_url="https://reddit.com/r/x/comments/1", actor="alice")
    from gw_geo.common.db import SeedingTask as T
    assert s.get(T, tid).status == SeedingStatus.PLACED

def test_blocked_proposal_cannot_be_placed():
    wf, s = _wf()
    tid = wf.create(brand_id="b1", channel="reddit")
    bad = PlacementProposal(channel="reddit", body="Acme rules", disclosure_text="",
                            author_is_real=False)                    # astroturf
    rep = wf.run_compliance(tid, bad)
    assert rep.passed is False
    with pytest.raises(ComplianceError):
        wf.mark_placed(tid, placed_url="https://reddit.com/x", actor="alice")   # gate holds

def test_mark_placed_requires_prior_compliance():
    wf, s = _wf()
    tid = wf.create(brand_id="b1", channel="g2")                    # never ran compliance
    with pytest.raises(ComplianceError):
        wf.mark_placed(tid, placed_url="https://g2.com/x", actor="bob")
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** the state machine over `seeding_task`; persist the `ComplianceReport` JSON;
  make `mark_placed` re-read and hard-assert `passed and status==READY_FOR_HUMAN`. No network; no
  auto-posting — `placed_url` is human-supplied.
- [ ] **4. Run → pass**; add a test that an illegal transition (e.g. `attach_brief` on a `PLACED` task)
  raises.
- [ ] **5. Commit:** `feat(seeding): compliance-gated human-in-the-loop placement workflow`

## Acceptance
- Happy path reaches `PLACED` only after a **passing** compliance report and a human `mark_placed`;
  blocked/never-checked proposals **cannot** be placed (`ComplianceError`); tenant-scoped; hermetic
  (no live posting). Gate is unbypassable — a tested PRD-NG1 contract.
