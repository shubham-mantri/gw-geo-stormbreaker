"""Billing views (m4-design §4.3): the read/query layer backing the Settings -> billing screen
(ui-spec §3.8, §7 M4 RaaS/billing views) -- current-period running total, usage breakdown, RaaS
contribution, and invoice history.

`billing_summary` composes T08 metering + an injected `AttributionSource` (M2) + T09
`compute_invoice` into a dashboard-ready dict; the attribution lookup is tenant-wide (`brand_id`
is not part of this module's interface, per m4-design §4.3), matching `billing_account`/
`billing_invoice` being tenant- rather than brand-scoped (m4-design §4.4). `invoice_history` reads
persisted `billing_invoice` rows, tenant-scoped, newest period first -- no recomputation, purely a
query over what `billing_summary`'s caller has already chosen to persist.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from gw_geo.billing.metering import meter_period
from gw_geo.billing.pricing import AttributionSource, PricingPlan, compute_invoice
from gw_geo.common.db import BillingInvoice


def billing_summary(
    session: Session,
    *,
    tenant_id: str,
    plan: PricingPlan,
    attribution: AttributionSource,
    period_start: str,
    period_end: str,
) -> dict[str, Any]:
    """Current-period billing summary: meter usage (T08), resolve attributed results via the
    injected `attribution` (M2), price the period (T09), and shape the result for the dashboard.
    """
    usage = meter_period(
        session, tenant_id=tenant_id, period_start=period_start, period_end=period_end
    )
    results = attribution.attributed_results(
        tenant_id=tenant_id, brand_id=None, period_start=period_start, period_end=period_end
    )
    invoice = compute_invoice(
        tenant_id=tenant_id,
        plan=plan,
        usage=usage,
        results=results,
        period_start=period_start,
        period_end=period_end,
    )

    return {
        "base_fee": invoice.base_fee,
        "usage_charges": invoice.usage_charges,
        "raas_charge": invoice.raas_charge,
        "attributed_leads": invoice.attributed_leads,
        "attributed_pipeline_usd": invoice.attributed_pipeline_usd,
        "total": invoice.total,
        "currency": invoice.currency,
        "period_start": invoice.period_start,
        "period_end": invoice.period_end,
    }


def invoice_history(
    session: Session, *, tenant_id: str, limit: int = 12
) -> list[dict[str, Any]]:
    """The tenant's persisted `billing_invoice` rows, newest period first, capped at `limit`."""
    rows = (
        session.query(BillingInvoice)
        .filter(BillingInvoice.tenant_id == tenant_id)
        .order_by(BillingInvoice.period_start.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "period_start": row.period_start,
            "period_end": row.period_end,
            "total": row.total,
            "raas_charge": row.raas_charge,
            "status": row.status,
        }
        for row in rows
    ]
