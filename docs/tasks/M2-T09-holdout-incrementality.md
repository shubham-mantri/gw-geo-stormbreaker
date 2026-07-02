# M2-T09 — Attribution mechanism 4: holdout incrementality

**Depends on:** T02 · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** Mechanism 4 (TRD §6, PRD §6.2 #4) — **the causal backbone**. Compare lead flow in an
un-optimized **holdout cohort** vs an optimized cohort → estimate **incremental lift with a CI**.
This is the only causal claim the product makes (PRD §13; "sell incrementality, not vanity").

**Files:**
- Create: `src/gw_geo/attribution/holdout.py`
- Test: `tests/attribution/test_holdout.py`

## Interface

```python
class HoldoutResult(BaseModel):
    cohort_id: str
    holdout_leads: int; optimized_leads: int
    n_holdout: int; n_optimized: int          # exposure denominators (sessions/prompts)
    lift_pct: float; ci_low: float; ci_high: float
    significant: bool

def measure_incrementality(session, *, tenant_id: str, brand_id: str, cohort_id: str,
                           since: str, until: str) -> HoldoutResult: ...
    # holdout_cohort(is_holdout) marks the un-optimized prompt/geo set; the complement is optimized.
    # lift_pct = (opt_rate - hold_rate) / hold_rate ; two-proportion CI (Wilson / bootstrap),
    # significant = CI excludes 0. Reuse M0 stats conventions (TRD §3).
```

## Steps
- [ ] **1. Failing test** `tests/attribution/test_holdout.py`:

```python
import pytest
from gw_geo.attribution.holdout import measure_incrementality, HoldoutResult

def test_positive_lift(seeded_holdout):
    # fixture: cohort c1; optimized rate ~0.30 (30/100), holdout rate ~0.10 (10/100)
    r = measure_incrementality(seeded_holdout, tenant_id="t1", brand_id="b1",
                               cohort_id="c1", since="2026-06-01", until="2026-07-02")
    assert isinstance(r, HoldoutResult)
    assert r.lift_pct > 1.0            # ~+200%
    assert r.ci_low <= r.lift_pct <= r.ci_high
    assert r.significant is True       # CI excludes 0

def test_no_effect_not_significant(seeded_equal_cohorts):
    r = measure_incrementality(seeded_equal_cohorts, tenant_id="t1", brand_id="b1",
                               cohort_id="c2", since="2026-06-01", until="2026-07-02")
    assert abs(r.lift_pct) < 0.2 and r.significant is False
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** cohort split via `holdout_cohort.is_holdout` + `prompt_ids`, two-proportion
  lift, CI (Wilson difference / bootstrap), significance = CI excludes 0. Tenant-scoped reads.
- [ ] **4. Run → pass**; add a property test (lift monotonic in optimized-cohort lead count; CI
  bounds finite). mypy clean on touched `common`.
- [ ] **5. Commit:** `feat(attribution): holdout incrementality (mechanism 4)`

## Acceptance
- Computes lift + CI from cohort lead rates; `significant` true iff CI excludes 0; equal cohorts →
  ~0 lift, not significant; tenant-scoped; hermetic; property test on monotonicity green.
