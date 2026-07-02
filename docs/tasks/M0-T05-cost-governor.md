# M0-T05 — Cost governor

**Depends on:** T02, T04 · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** Enforce per-tenant daily sampling budget before spending on probes. Probing is the
dominant cost — this guard is mandatory (TRD §7).

**Files:**
- Create: `src/gw_geo/common/budget.py`
- Test: `tests/common/test_budget.py`

## Interface

```python
class BudgetExceeded(Exception): ...

class CostGovernor:
    def __init__(self, session, tenant_id: str) -> None: ...
    def spent_today(self) -> float: ...                 # sum(probe_run.cost_usd) for today
    def remaining(self) -> float: ...                    # tenant.sampling_budget_daily - spent_today
    def check(self, estimated_cost: float) -> None: ...  # raise BudgetExceeded if over
    def can_afford(self, estimated_cost: float) -> bool: ...
```

## Steps
- [ ] **1. Failing test** `tests/common/test_budget.py`:

```python
import pytest
from gw_geo.common.budget import CostGovernor, BudgetExceeded
# helpers: seed a Tenant(sampling_budget_daily=1.0) and ProbeRun rows via a SQLite session fixture

def test_remaining_after_spend(seeded_session):
    gov = CostGovernor(seeded_session, "t1")   # 0.30 already spent today
    assert round(gov.remaining(), 2) == 0.70

def test_check_raises_when_over(seeded_session):
    gov = CostGovernor(seeded_session, "t1")
    with pytest.raises(BudgetExceeded):
        gov.check(estimated_cost=0.90)
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** using the T04 session/tables; "today" = UTC date on `probe_run.ts`.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(common): per-tenant cost governor`

## Acceptance
- `remaining()` = budget − today's spend; `check()` raises `BudgetExceeded` when the estimate
  exceeds remaining; `can_afford()` mirrors it without raising.
