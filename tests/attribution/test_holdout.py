"""Tests for holdout incrementality (M2-T09, m2-design §2.5) -- attribution mechanism 4, the
causal backbone. Exercised against a hermetic in-memory SQLite database (TRD §12).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SASession

from gw_geo.attribution.holdout import HoldoutResult, _relative_lift_ci, measure_incrementality
from gw_geo.common.db import Base, HoldoutCohort
from gw_geo.common.db import Lead as LeadRow
from gw_geo.common.db import Session as SessionRow
from gw_geo.common.db import TenantScopedSession

_WINDOW_TS = datetime(2026, 6, 15, tzinfo=timezone.utc)


def _seed_cohort_traffic(
    raw: SASession,
    *,
    cohort_id: str,
    holdout_prompt_id: str,
    holdout_sessions: int,
    holdout_leads: int,
    optimized_sessions: int,
    optimized_leads: int,
) -> None:
    """Seed one `holdout_cohort` (is_holdout=True, prompt_ids=[holdout_prompt_id]) plus
    `holdout_sessions`/`optimized_sessions` `session` rows tagged via `utm["prompt_id"]` (holdout
    tag in `prompt_ids`, optimized tag outside it) and a `lead` for the first `*_leads` sessions on
    each side -- so `holdout_leads`/`optimized_leads` land exactly on the requested counts.
    """
    raw.add(
        HoldoutCohort(
            id=cohort_id,
            tenant_id="t1",
            brand_id="b1",
            name=cohort_id,
            kind="prompt",
            prompt_ids=[holdout_prompt_id],
            is_holdout=True,
        )
    )
    sides = [
        ("hold", holdout_prompt_id, holdout_sessions, holdout_leads),
        ("opt", f"{holdout_prompt_id}-complement", optimized_sessions, optimized_leads),
    ]
    for side, prompt_id, n_sessions, n_leads in sides:
        for i in range(n_sessions):
            sid = f"{cohort_id}-{side}-s{i}"
            raw.add(
                SessionRow(
                    id=sid,
                    tenant_id="t1",
                    brand_id="b1",
                    visitor_id=f"v-{sid}",
                    landing_url="https://acme.com/page",
                    utm={"prompt_id": prompt_id},
                    ts=_WINDOW_TS,
                )
            )
            if i < n_leads:
                raw.add(
                    LeadRow(
                        id=f"{sid}-lead",
                        tenant_id="t1",
                        brand_id="b1",
                        visitor_id=f"v-{sid}",
                        session_id=sid,
                        ts=_WINDOW_TS,
                    )
                )
    raw.commit()


@pytest.fixture
def seeded_holdout_raw() -> SASession:
    """cohort c1: optimized rate 30/100 (~0.30), holdout rate 10/100 (~0.10)."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    raw = SASession(engine)
    _seed_cohort_traffic(
        raw,
        cohort_id="c1",
        holdout_prompt_id="p-c1-holdout",
        holdout_sessions=100,
        holdout_leads=10,
        optimized_sessions=100,
        optimized_leads=30,
    )
    return raw


@pytest.fixture
def seeded_holdout(seeded_holdout_raw: SASession) -> TenantScopedSession:
    return TenantScopedSession(seeded_holdout_raw, "t1")


@pytest.fixture
def seeded_equal_cohorts() -> TenantScopedSession:
    """cohort c2: equal 15/100 rate on both sides -- no real effect."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    raw = SASession(engine)
    _seed_cohort_traffic(
        raw,
        cohort_id="c2",
        holdout_prompt_id="p-c2-holdout",
        holdout_sessions=100,
        holdout_leads=15,
        optimized_sessions=100,
        optimized_leads=15,
    )
    return TenantScopedSession(raw, "t1")


def test_positive_lift(seeded_holdout: TenantScopedSession) -> None:
    r = measure_incrementality(
        seeded_holdout,
        tenant_id="t1",
        brand_id="b1",
        cohort_id="c1",
        since="2026-06-01",
        until="2026-07-02",
    )
    assert isinstance(r, HoldoutResult)
    assert r.n_holdout == 100 and r.n_optimized == 100
    assert r.holdout_leads == 10 and r.optimized_leads == 30
    assert r.lift_pct > 1.0  # ~+200%
    assert r.ci_low <= r.lift_pct <= r.ci_high
    assert r.significant is True  # CI excludes 0


def test_no_effect_not_significant(seeded_equal_cohorts: TenantScopedSession) -> None:
    r = measure_incrementality(
        seeded_equal_cohorts,
        tenant_id="t1",
        brand_id="b1",
        cohort_id="c2",
        since="2026-06-01",
        until="2026-07-02",
    )
    assert abs(r.lift_pct) < 0.2 and r.significant is False
    assert r.ci_low < 0.0 < r.ci_high  # CI straddles 0


def test_wrong_tenant_cannot_read_cohort(seeded_holdout_raw: SASession) -> None:
    """TRD §7: reads go through `TenantScopedSession` -- a `t2`-scoped session can't see `t1`'s
    holdout_cohort row at all, so the lookup fails closed rather than leaking a cross-tenant
    result."""
    other_tenant_session = TenantScopedSession(seeded_holdout_raw, "t2")
    with pytest.raises(ValueError):
        measure_incrementality(
            other_tenant_session,
            tenant_id="t2",
            brand_id="b1",
            cohort_id="c1",
            since="2026-06-01",
            until="2026-07-02",
        )


def test_lift_monotonic_in_optimized_lead_count_and_ci_bounds_finite() -> None:
    """Lift math property (m2-design §10 testing strategy): holding the holdout side and
    `n_optimized` fixed, `lift_pct` is monotonically increasing in `optimized_leads`, every CI
    bound stays finite, and `ci_low <= lift_pct <= ci_high` always holds -- including at the
    zero-holdout-rate and zero-exposure edge cases where relative lift is undefined and the
    fallback must still be finite rather than `inf`/`nan`.
    """
    n_holdout, holdout_leads, n_optimized = 100, 10, 100

    prev_lift = float("-inf")
    for optimized_leads in range(0, n_optimized + 1, 5):
        lift_pct, ci_low, ci_high = _relative_lift_ci(
            holdout_leads, n_holdout, optimized_leads, n_optimized
        )
        assert math.isfinite(lift_pct)
        assert math.isfinite(ci_low)
        assert math.isfinite(ci_high)
        assert ci_low <= lift_pct <= ci_high
        assert lift_pct > prev_lift
        prev_lift = lift_pct

    # Zero-holdout-rate edge case: relative lift is undefined (division by zero); the finite
    # fallback (absolute rate difference) must still hold.
    for optimized_leads in (0, 1, 50, 100):
        lift_pct, ci_low, ci_high = _relative_lift_ci(0, n_holdout, optimized_leads, n_optimized)
        assert math.isfinite(lift_pct) and math.isfinite(ci_low) and math.isfinite(ci_high)
        assert ci_low <= lift_pct <= ci_high

    # Zero exposure denominators on both sides must not explode either.
    lift_pct, ci_low, ci_high = _relative_lift_ci(0, 0, 0, 0)
    assert math.isfinite(lift_pct) and math.isfinite(ci_low) and math.isfinite(ci_high)
