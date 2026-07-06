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
from gw_geo.common.db import Base, Brand, HoldoutCohort, Tenant
from gw_geo.common.db import Lead as LeadRow
from gw_geo.common.db import Session as SessionRow
from gw_geo.common.db import TenantScopedSession

_WINDOW_TS = datetime(2026, 6, 15, tzinfo=timezone.utc)


def _seed_cohort_traffic(
    raw: SASession,
    *,
    cohort_id: str,
    holdout_prompt_id: str,
    optimized_prompt_id: str,
    holdout_sessions: int,
    holdout_leads: int,
    optimized_sessions: int,
    optimized_leads: int,
    noise_sessions: int = 0,
    noise_leads: int = 0,
    seed_optimized_cohort: bool = True,
) -> None:
    """Seed a *symmetric* holdout experiment for brand ``b1`` / tenant ``t1``:

    * a **holdout** cohort ``cohort_id`` (``is_holdout=True``, ``prompt_ids=[holdout_prompt_id]``);
    * an **optimized** cohort ``f"{cohort_id}-opt"`` (``is_holdout=False``,
      ``prompt_ids=[optimized_prompt_id]``) -- unless ``seed_optimized_cohort=False``, which leaves
      the brand with no optimized arm at all;
    * ``holdout_sessions`` / ``optimized_sessions`` ``session`` rows tagged via ``utm["prompt_id"]``
      with the matching cohort's prompt, and a ``lead`` for the first ``*_leads`` of each side -- so
      ``holdout_leads`` / ``optimized_leads`` land exactly on the requested counts;
    * ``noise_sessions`` **non-cohort** sessions -- half untagged organic/direct (``utm={}``), half
      tagged to a foreign prompt in *neither* cohort -- with ``noise_leads`` conversions among them.

    That noise is exactly the "untagged, non-experiment traffic" the fixed optimized arm must
    **exclude** from both arms; the old definition (all non-holdout traffic) wrongly swept it into
    the optimized denominator and biased the causal lift.
    """
    raw.add(Tenant(id="t1", name="Acme", sampling_budget_daily=100.0))
    raw.add(Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com", competitors=[]))
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
    if seed_optimized_cohort:
        raw.add(
            HoldoutCohort(
                id=f"{cohort_id}-opt",
                tenant_id="t1",
                brand_id="b1",
                name=f"{cohort_id}-opt",
                kind="prompt",
                prompt_ids=[optimized_prompt_id],
                is_holdout=False,
            )
        )

    def _tag(side: str, i: int) -> dict[str, str]:
        # holdout/opt sessions carry their cohort's prompt tag; "noise" belongs to no cohort --
        # alternately untagged organic/direct (utm={}) and tagged to a foreign, non-cohort prompt.
        if side == "hold":
            return {"prompt_id": holdout_prompt_id}
        if side == "opt":
            return {"prompt_id": optimized_prompt_id}
        return {} if i % 2 == 0 else {"prompt_id": f"{cohort_id}-foreign"}

    sides = [
        ("hold", holdout_sessions, holdout_leads),
        ("opt", optimized_sessions, optimized_leads),
        ("noise", noise_sessions, noise_leads),
    ]
    for side, n_sessions, n_leads in sides:
        for i in range(n_sessions):
            sid = f"{cohort_id}-{side}-s{i}"
            raw.add(
                SessionRow(
                    id=sid,
                    tenant_id="t1",
                    brand_id="b1",
                    visitor_id=f"v-{sid}",
                    landing_url="https://acme.com/page",
                    utm=_tag(side, i),
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
    """cohort c1: optimized cohort rate 30/100 (~0.30) vs holdout cohort rate 10/100 (~0.10), plus
    60 non-cohort "noise" sessions (untagged + foreign-tagged) with 50 conversions. The noise is
    excluded from both arms: if it leaked into the optimized arm (the old bug), n_optimized would be
    160 and optimized_leads 80, not 100 and 30."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    raw = SASession(engine)
    _seed_cohort_traffic(
        raw,
        cohort_id="c1",
        holdout_prompt_id="p-c1-holdout",
        optimized_prompt_id="p-c1-optimized",
        holdout_sessions=100,
        holdout_leads=10,
        optimized_sessions=100,
        optimized_leads=30,
        noise_sessions=60,
        noise_leads=50,
    )
    return raw


@pytest.fixture
def seeded_holdout(seeded_holdout_raw: SASession) -> TenantScopedSession:
    return TenantScopedSession(seeded_holdout_raw, "t1")


@pytest.fixture
def seeded_equal_cohorts() -> TenantScopedSession:
    """cohort c2: equal 15/100 rate on both cohort arms -- no real effect (plus noise it excludes)."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    raw = SASession(engine)
    _seed_cohort_traffic(
        raw,
        cohort_id="c2",
        holdout_prompt_id="p-c2-holdout",
        optimized_prompt_id="p-c2-optimized",
        holdout_sessions=100,
        holdout_leads=15,
        optimized_sessions=100,
        optimized_leads=15,
        noise_sessions=40,
        noise_leads=30,
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
    # Both arms are their own cohort ONLY -- the 60 non-cohort noise sessions (with 50 conversions)
    # are excluded from both, so n_optimized stays 100 / 30 (not 160 / 80 as the old all-non-holdout
    # definition would have produced).
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
    assert r.n_holdout == 100 and r.n_optimized == 100  # noise excluded from both arms
    assert abs(r.lift_pct) < 0.2 and r.significant is False
    assert r.ci_low < 0.0 < r.ci_high  # CI straddles 0


def test_untagged_and_noncohort_traffic_excluded_from_optimized_arm() -> None:
    """The core of the fix (m2-design §2.5): the optimized arm is the optimized *cohort*, not "all
    non-holdout traffic". Untagged organic/direct sessions and sessions tagged to a prompt in
    neither cohort are excluded from BOTH arms -- so a large pile of high-converting noise leaves
    ``n_optimized`` / ``optimized_leads`` untouched. Under the old definition that noise would have
    dominated the optimized denominator and destroyed the causal comparison.
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    raw = SASession(engine)
    _seed_cohort_traffic(
        raw,
        cohort_id="cx",
        holdout_prompt_id="p-hold",
        optimized_prompt_id="p-opt",
        holdout_sessions=40,
        holdout_leads=4,  # holdout cohort rate 0.10
        optimized_sessions=40,
        optimized_leads=12,  # optimized cohort rate 0.30
        noise_sessions=200,
        noise_leads=200,  # 100%-converting noise: would swamp the optimized arm if included
    )
    scoped = TenantScopedSession(raw, "t1")
    r = measure_incrementality(
        scoped, tenant_id="t1", brand_id="b1", cohort_id="cx", since="2026-06-01", until="2026-07-02"
    )
    # Optimized arm == the optimized cohort ONLY (40 sessions / 12 leads); the 200 noise sessions
    # and their 200 leads are in NEITHER arm.
    assert r.n_optimized == 40 and r.optimized_leads == 12
    assert r.n_holdout == 40 and r.holdout_leads == 4
    # Lift reflects only the two cohorts: (0.30 - 0.10) / 0.10 = +200%, not diluted by the noise.
    assert r.lift_pct == pytest.approx(2.0, abs=1e-6)
    assert r.significant is True


def test_no_optimized_cohort_degrades_to_zero_lift() -> None:
    """No optimized cohort for the brand -> the optimized arm is empty -> report a graceful zero
    lift (``lift_pct == 0.0``, zero-width CI, ``significant`` False), explicitly NOT a fall-back to
    "all non-holdout traffic". A pile of high-converting untagged/foreign noise must not become the
    optimized arm.
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    raw = SASession(engine)
    _seed_cohort_traffic(
        raw,
        cohort_id="cy",
        holdout_prompt_id="p-hold",
        optimized_prompt_id="p-opt",  # unused: no optimized cohort is seeded
        holdout_sessions=100,
        holdout_leads=10,
        optimized_sessions=0,
        optimized_leads=0,
        noise_sessions=80,
        noise_leads=70,
        seed_optimized_cohort=False,
    )
    scoped = TenantScopedSession(raw, "t1")
    r = measure_incrementality(
        scoped, tenant_id="t1", brand_id="b1", cohort_id="cy", since="2026-06-01", until="2026-07-02"
    )
    assert r.n_optimized == 0 and r.optimized_leads == 0  # noise NOT swept into the optimized arm
    assert r.n_holdout == 100 and r.holdout_leads == 10  # holdout arm still measured
    assert r.lift_pct == 0.0
    assert r.ci_low == 0.0 and r.ci_high == 0.0
    assert r.significant is False


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
