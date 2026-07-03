"""Tests for billing views (docs/tasks/M4-T13-billing-views.md, m4-design §4.3): the read/query
layer backing the Settings -> billing screen. `billing_summary` composes T08 metering + an
injected `AttributionSource` + T09 `compute_invoice` into a dashboard-ready dict; `invoice_history`
reads persisted `billing_invoice` rows, tenant-scoped, newest first.
"""

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.billing.metering import UsageKind, record_usage
from gw_geo.billing.pricing import AttributedResults, PricingPlan
from gw_geo.billing.views import billing_summary, invoice_history
from gw_geo.common.db import Base, BillingInvoice


class FakeAttribution:
    def attributed_results(self, *, tenant_id, brand_id, period_start, period_end):
        return AttributedResults(attributed_leads=100, attributed_pipeline_usd=300000.0)


def _session() -> Session:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return Session(eng)


def test_billing_summary_composes_usage_and_raas():
    s = _session()
    record_usage(
        s, tenant_id="t1", brand_id="b1", kind=UsageKind.PROBE, quantity=1000, ts="2026-06-10"
    )
    s.commit()
    plan = PricingPlan(
        plan="enterprise", base_fee=1000.0, usage_rates={"probe": 0.001},
        raas_enabled=True, raas_basis="per_lead", raas_rate=20.0
    )
    out = billing_summary(
        s, tenant_id="t1", plan=plan, attribution=FakeAttribution(),
        period_start="2026-06-01", period_end="2026-07-01"
    )
    assert out["usage_charges"]["probe"] == 1.0
    assert out["raas_charge"] == 100 * 20.0
    assert out["total"] == 1000.0 + 1.0 + 2000.0 and out["attributed_leads"] == 100


def test_invoice_history_newest_first():
    s = _session()
    s.add(BillingInvoice(
        id="i1", tenant_id="t1", period_start="2026-05-01",
        period_end="2026-06-01", base_fee=1000.0, usage_charges={}, raas_charge=0.0,
        attributed_leads=0, attributed_pipeline_usd=0.0, total=1000.0, status="paid"
    ))
    s.add(BillingInvoice(
        id="i2", tenant_id="t1", period_start="2026-06-01",
        period_end="2026-07-01", base_fee=1000.0, usage_charges={}, raas_charge=500.0,
        attributed_leads=25, attributed_pipeline_usd=0.0, total=1500.0, status="open"
    ))
    s.commit()
    hist = invoice_history(s, tenant_id="t1")
    assert [h["period_start"] for h in hist] == ["2026-06-01", "2026-05-01"]


def test_invoice_history_is_deterministic_on_ties():
    # fix 8: rows sharing period_start AND created_at must order deterministically -- the stable
    # final tiebreaker is `id` ascending, so `total` (distinct per row) comes back i_a, i_b, i_c.
    s = _session()
    ts = datetime(2026, 7, 1, tzinfo=timezone.utc)
    for inv_id, total in (("i_c", 3.0), ("i_a", 1.0), ("i_b", 2.0)):
        s.add(BillingInvoice(
            id=inv_id, tenant_id="t1", period_start="2026-06-01", period_end="2026-07-01",
            base_fee=0.0, usage_charges={}, raas_charge=0.0, attributed_leads=0,
            attributed_pipeline_usd=0.0, total=total, status="draft", created_at=ts,
        ))
    s.commit()
    hist = invoice_history(s, tenant_id="t1")
    assert [h["total"] for h in hist] == [1.0, 2.0, 3.0]
    assert invoice_history(s, tenant_id="t1") == hist  # repeatable
