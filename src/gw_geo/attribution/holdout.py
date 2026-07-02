"""Attribution mechanism 4 (TRD §6 #4, m2-design §2.5): holdout incrementality.

Compares lead-conversion rate in a deliberately un-optimized **holdout** prompt cohort against the
**optimized** complement (every other tracked-prompt session for the brand in the window) and
reports the relative lift with a two-proportion confidence interval. This is the only mechanism in
`attribution/` that supports a causal claim (PRD §13, m2-design §1); direct referral, citation
linkage and assisted modeling are all correlational.

**Cohort membership.** Neither `session` nor `lead` carries a `prompt_id` column (that join only
exists via `attribution_link`, written by the *other* mechanisms -- out of scope here; T09 depends
on T02 only, not T05/T06/T07). Holdout incrementality is designed to stand apart from those fuzzier
attribution links entirely: it is a controlled-experiment read over raw traffic, not a credit-
assignment model. The join this module needs -- "which tracked prompt drove this session" -- is
carried directly on `session.utm["prompt_id"]`, populated by the lead-capture pixel when the
landing page targets a prompt under a live holdout experiment (m2-design §2.1). A session whose
`utm["prompt_id"]` is in `holdout_cohort.prompt_ids` falls on whichever side of the split
`is_holdout` marks that cohort row as; every other brand session in the window (including
untagged, non-experiment traffic) is the complement side. A `lead` counts toward a side when its
`session_id` falls in that side's session-id set -- so the unit of analysis is "did this exposure
(session) convert to a lead", the same proportion-with-sample-size shape as every other metric in
this codebase (TRD §3).

This join convention (`utm["prompt_id"]`) is an implementation decision, not a TRD-pinned contract
-- flagged in the task report for the orchestrator/user to confirm it stays consistent with
whatever `ingest.py`/`referral.py` (T05/T06) end up writing into `session.utm`.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from gw_geo.common.db import HoldoutCohort, Lead, Session, TenantScopedSession
from gw_geo.measurement.aggregate import wilson_ci


class HoldoutResult(BaseModel):
    cohort_id: str
    holdout_leads: int
    optimized_leads: int
    n_holdout: int
    n_optimized: int  # exposure denominators (sessions)
    lift_pct: float
    ci_low: float
    ci_high: float
    significant: bool


def _inclusive_window(since: str, until: str) -> tuple[datetime, datetime]:
    """`[since, until]` inclusive UTC day bounds as a half-open `(start, end)` datetime range.

    Same `YYYY-MM-DD`, inclusive-ends convention as `measurement/feed.py`'s
    `_inclusive_date_bounds` (TRD §5).
    """
    start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    return start, end


def _relative_lift_ci(
    holdout_leads: int, n_holdout: int, optimized_leads: int, n_optimized: int
) -> tuple[float, float, float]:
    """Pure two-proportion lift + CI core (no I/O) -- reuses M0's `wilson_ci` (TRD §3).

    `lift_pct` is the relative change in conversion rate, `(opt_rate - hold_rate) / hold_rate`.
    Its CI is built from the Newcombe/Wilson hybrid score interval for the *difference* of two
    independent proportions -- each side's own Wilson interval, combined per Newcombe (1998):
    given Wilson intervals `(l1, u1)` for `p1` and `(l2, u2)` for `p2`, the interval for `p1 - p2`
    is `(p1 - p2 - sqrt((p1-l1)^2 + (u2-p2)^2), p1 - p2 + sqrt((u1-p1)^2 + (p2-l2)^2))`. That
    difference-scale interval is then rescaled onto the relative-lift scale by the (point-
    estimate) holdout rate, holding it fixed -- a standard approximation for relative-risk-like
    ratios that keeps this closed-form (no bootstrap resampling needed).

    When the holdout rate is exactly `0.0`, relative lift is mathematically undefined (division by
    zero); the fallback reports the absolute rate difference instead so the result stays finite
    and directionally honest rather than raising or returning `inf`/`nan`.
    """
    hold_rate = holdout_leads / n_holdout if n_holdout else 0.0
    opt_rate = optimized_leads / n_optimized if n_optimized else 0.0

    hold_lo, hold_hi = wilson_ci(holdout_leads, n_holdout)
    opt_lo, opt_hi = wilson_ci(optimized_leads, n_optimized)

    diff = opt_rate - hold_rate
    diff_low = diff - math.sqrt((opt_rate - opt_lo) ** 2 + (hold_hi - hold_rate) ** 2)
    diff_high = diff + math.sqrt((opt_hi - opt_rate) ** 2 + (hold_rate - hold_lo) ** 2)

    if hold_rate == 0.0:
        return diff, diff_low, diff_high
    return diff / hold_rate, diff_low / hold_rate, diff_high / hold_rate


def measure_incrementality(
    session: TenantScopedSession,
    *,
    tenant_id: str,
    brand_id: str,
    cohort_id: str,
    since: str,
    until: str,
) -> HoldoutResult:
    """Mechanism 4 (TRD §6 #4): holdout-vs-optimized incremental lift with a CI.

    `session` must already be a `TenantScopedSession` bound to `tenant_id` (TRD §7); every read
    below goes through it (plus an explicit `brand_id` filter), so no cross-tenant/-brand row can
    leak into the result. Raises `ValueError` if `session` is scoped to a different tenant, or if
    no `holdout_cohort` row matches `(brand_id, cohort_id)` for this tenant.
    """
    if session.tenant_id != tenant_id:
        raise ValueError(f"session is scoped to tenant_id={session.tenant_id!r}, not {tenant_id!r}")

    cohort = (
        session.query(HoldoutCohort)
        .filter(HoldoutCohort.brand_id == brand_id, HoldoutCohort.id == cohort_id)
        .first()
    )
    if cohort is None:
        raise ValueError(f"no holdout_cohort {cohort_id!r} for brand {brand_id!r}")

    start, end = _inclusive_window(since, until)
    cohort_prompt_ids = set(cohort.prompt_ids)

    sessions = (
        session.query(Session)
        .filter(Session.brand_id == brand_id, Session.ts >= start, Session.ts < end)
        .all()
    )
    marked_ids = {s.id for s in sessions if s.utm.get("prompt_id") in cohort_prompt_ids}
    other_ids = {s.id for s in sessions if s.id not in marked_ids}
    holdout_session_ids = marked_ids if cohort.is_holdout else other_ids
    optimized_session_ids = other_ids if cohort.is_holdout else marked_ids

    leads = (
        session.query(Lead).filter(Lead.brand_id == brand_id, Lead.ts >= start, Lead.ts < end).all()
    )
    holdout_leads = sum(1 for lead in leads if lead.session_id in holdout_session_ids)
    optimized_leads = sum(1 for lead in leads if lead.session_id in optimized_session_ids)

    n_holdout = len(holdout_session_ids)
    n_optimized = len(optimized_session_ids)
    lift_pct, ci_low, ci_high = _relative_lift_ci(
        holdout_leads, n_holdout, optimized_leads, n_optimized
    )

    return HoldoutResult(
        cohort_id=cohort_id,
        holdout_leads=holdout_leads,
        optimized_leads=optimized_leads,
        n_holdout=n_holdout,
        n_optimized=n_optimized,
        lift_pct=lift_pct,
        ci_low=ci_low,
        ci_high=ci_high,
        significant=ci_low > 0.0 or ci_high < 0.0,
    )
