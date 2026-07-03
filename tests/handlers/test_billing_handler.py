"""Billing period-close Lambda handler tests (m4-design §4.2/§4.4,
docs/tasks/M4-T16-handlers-serverless.md).

Hermetic (TRD §12): `session`/`plan`/`attribution` are all injected via `deps` (a SQLite session
+ fakes), so the handler never builds `Settings`/wiring here -- no live network/DB. See
`gw_geo.handlers.close_billing`'s module docstring for how the `deps=None` production path builds
its collaborators instead.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.billing.metering import UsageKind, record_usage
from gw_geo.billing.pricing import AttributedResults, PricingPlan
from gw_geo.common.db import Base, BillingInvoice
from gw_geo.handlers.close_billing import handler


class FakeAttribution:
    def attributed_results(self, *, tenant_id, brand_id, period_start, period_end):
        return AttributedResults(attributed_leads=10, attributed_pipeline_usd=0.0)


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_billing_handler_persists_invoice() -> None:
    session = _session()
    plan = PricingPlan(plan="growth", base_fee=500.0, usage_rates={})

    out = handler(
        {"tenant_id": "t1", "period_start": "2026-06-01", "period_end": "2026-07-01"},
        deps={"session": session, "plan": plan, "attribution": FakeAttribution()},
    )

    assert out["statusCode"] == 200
    assert session.query(BillingInvoice).count() == 1
    assert out["body"]["total"] == 500.0


def test_billing_handler_prices_usage_and_raas_into_persisted_invoice() -> None:
    session = _session()
    record_usage(
        session,
        tenant_id="t1",
        brand_id="b1",
        kind=UsageKind.PROBE,
        quantity=500,
        ts="2026-06-15",
    )
    session.commit()
    plan = PricingPlan(
        plan="enterprise",
        base_fee=200.0,
        usage_rates={"probe": 0.01},
        raas_enabled=True,
        raas_basis="per_lead",
        raas_rate=5.0,
    )

    out = handler(
        {"tenant_id": "t1", "period_start": "2026-06-01", "period_end": "2026-07-01"},
        deps={"session": session, "plan": plan, "attribution": FakeAttribution()},
    )

    invoice = session.get(BillingInvoice, out["body"]["invoice_id"])
    assert invoice is not None
    assert invoice.usage_charges == {"probe": 5.0}
    assert invoice.raas_charge == 50.0
    assert out["body"]["total"] == 200.0 + 5.0 + 50.0


def test_billing_handler_is_idempotent_per_period() -> None:
    # A retried monthly cron must not double-insert a draft invoice for the same period.
    session = _session()
    plan = PricingPlan(plan="growth", base_fee=500.0, usage_rates={})
    event = {"tenant_id": "t1", "period_start": "2026-06-01", "period_end": "2026-07-01"}

    out1 = handler(event, deps={"session": session, "plan": plan, "attribution": FakeAttribution()})
    out2 = handler(event, deps={"session": session, "plan": plan, "attribution": FakeAttribution()})

    assert session.query(BillingInvoice).count() == 1
    assert out1["body"]["invoice_id"] == out2["body"]["invoice_id"]
    assert out2["statusCode"] == 200


def test_billing_handler_scopes_usage_by_tenant_and_period() -> None:
    session = _session()
    record_usage(
        session,
        tenant_id="other-tenant",
        brand_id="b1",
        kind=UsageKind.PROBE,
        quantity=999,
        ts="2026-06-15",
    )
    session.commit()
    plan = PricingPlan(plan="starter", base_fee=0.0, usage_rates={"probe": 1.0})

    out = handler(
        {"tenant_id": "t1", "period_start": "2026-06-01", "period_end": "2026-07-01"},
        deps={"session": session, "plan": plan, "attribution": FakeAttribution()},
    )

    assert out["body"]["total"] == 0.0
