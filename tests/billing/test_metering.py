"""Tests for usage metering (docs/tasks/M4-T08-usage-metering.md, m4-design §4.1): `record_usage`
writes a `UsageEvent`; `meter_period` sums `quantity` by `kind` for a tenant within a half-open
`[period_start, period_end)` window.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.billing.metering import UsageKind, meter_period, record_usage
from gw_geo.common.db import Base, Brand, Tenant


def _session() -> Session:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return Session(eng)


def test_meter_sums_by_kind_within_period() -> None:
    s = _session()
    s.add(Tenant(id="t1", name="t", sampling_budget_daily=100.0))
    s.add(Brand(id="b1", tenant_id="t1", name="b", domain="b.com"))
    s.commit()
    record_usage(
        s, tenant_id="t1", brand_id="b1", kind=UsageKind.PROBE, quantity=100, ts="2026-06-05"
    )
    record_usage(
        s, tenant_id="t1", brand_id="b1", kind=UsageKind.PROBE, quantity=50, ts="2026-06-20"
    )
    record_usage(
        s,
        tenant_id="t1",
        brand_id="b1",
        kind=UsageKind.SEEDING_PLACEMENT,
        quantity=3,
        ts="2026-06-20",
    )
    record_usage(
        s, tenant_id="t1", brand_id="b1", kind=UsageKind.PROBE, quantity=999, ts="2026-07-02"
    )  # out of period
    s.commit()

    summ = meter_period(s, tenant_id="t1", period_start="2026-06-01", period_end="2026-07-01")

    assert summ.by_kind["probe"] == 150 and summ.by_kind["seeding_placement"] == 3


def test_meter_scopes_to_tenant() -> None:
    s = _session()
    s.add(Tenant(id="t2", name="t", sampling_budget_daily=100.0))
    s.commit()
    record_usage(
        s, tenant_id="t2", brand_id=None, kind=UsageKind.GENERATION, quantity=5, ts="2026-06-10"
    )
    s.commit()

    assert (
        meter_period(s, tenant_id="t1", period_start="2026-06-01", period_end="2026-07-01").by_kind
        == {}
    )
