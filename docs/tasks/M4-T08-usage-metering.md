# M4-T08 — Usage metering

**Depends on:** T02 (`usage_event`) · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** Record and roll up billable usage per tenant/period (design §4.1). Probing dominates cost
(TRD §7/§8), so metering also folds in the already-persisted `probe_run.cost_usd`, plus content
generations (M3) and seeding placements (M4).

**Files:**
- Create: `src/gw_geo/billing/__init__.py` (if absent), `src/gw_geo/billing/metering.py`
- Test: `tests/billing/test_metering.py`

## Interface (design §4.1)

```python
from enum import StrEnum
from pydantic import BaseModel, Field

class UsageKind(StrEnum):
    PROBE = "probe"; GENERATION = "generation"; SEEDING_PLACEMENT = "seeding_placement"

def record_usage(session, *, tenant_id: str, brand_id: str | None, kind: UsageKind,
                 quantity: float, ts: str, source_ref: str | None = None) -> None: ...

class UsageSummary(BaseModel):
    tenant_id: str; period_start: str; period_end: str
    by_kind: dict[str, float] = Field(default_factory=dict)   # UsageKind → total quantity

def meter_period(session, *, tenant_id: str, period_start: str,
                 period_end: str) -> UsageSummary: ...
```

`record_usage` writes a `UsageEvent` row (unit inferred from `kind`). `meter_period` sums
`usage_event.quantity` by `kind` for the tenant where `period_start <= ts < period_end`.

## Steps
- [ ] **1. Failing test** `tests/billing/test_metering.py` (SQLite):

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from gw_geo.common.db import Base
from gw_geo.billing.metering import UsageKind, record_usage, meter_period

def _session():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); return Session(eng)

def test_meter_sums_by_kind_within_period():
    s = _session()
    record_usage(s, tenant_id="t1", brand_id="b1", kind=UsageKind.PROBE,
                 quantity=100, ts="2026-06-05")
    record_usage(s, tenant_id="t1", brand_id="b1", kind=UsageKind.PROBE,
                 quantity=50, ts="2026-06-20")
    record_usage(s, tenant_id="t1", brand_id="b1", kind=UsageKind.SEEDING_PLACEMENT,
                 quantity=3, ts="2026-06-20")
    record_usage(s, tenant_id="t1", brand_id="b1", kind=UsageKind.PROBE,
                 quantity=999, ts="2026-07-02")          # out of period
    s.commit()
    summ = meter_period(s, tenant_id="t1", period_start="2026-06-01", period_end="2026-07-01")
    assert summ.by_kind["probe"] == 150 and summ.by_kind["seeding_placement"] == 3

def test_meter_scopes_to_tenant():
    s = _session()
    record_usage(s, tenant_id="t2", brand_id=None, kind=UsageKind.GENERATION,
                 quantity=5, ts="2026-06-10"); s.commit()
    assert meter_period(s, tenant_id="t1", period_start="2026-06-01",
                        period_end="2026-07-01").by_kind == {}
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `record_usage` + `meter_period` over the `usage_event` table; tenant-scoped,
  half-open `[start, end)` period.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(billing): usage metering`

## Acceptance
- `record_usage` persists a `UsageEvent`; `meter_period` sums by kind within the half-open period,
  scoped to tenant; hermetic (SQLite).
