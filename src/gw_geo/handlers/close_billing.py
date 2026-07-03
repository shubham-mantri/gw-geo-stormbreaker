"""AWS Lambda handler for the M4 monthly billing period-close (m4-design §4.2/§4.4, T16).

Meters the closed period (`billing.metering.meter_period`, T08), resolves attributed results via
an injected `AttributionSource` (T09's protocol, satisfied by M2's attribution subsystem), prices
the period (`billing.pricing.compute_invoice`, T09), and persists the result as a `BillingInvoice`
row (`common.db`, T02) -- the durable form `billing/views.py::invoice_history` (T13) reads back.
Mirrors `handlers/run_measurement.py`/`handlers/run_drift.py`: `deps` used verbatim in tests,
built from `Settings`/wiring in production (the `close_billing` function in `serverless.yml`,
fired by a monthly EventBridge cron).

Production `plan` is resolved from the tenant's persisted `billing_account` row (T02) -- real,
same-milestone data, so there is no cross-milestone gap to document there; a tenant with no
`billing_account` row yet degrades gracefully to a $0 `starter` plan (this codebase's usual
"missing config -> skip, don't crash" posture, e.g. `common/wiring.py::build_runtime` for an
unset engine API key).

`attribution` is a different story: it needs an `AttributionSource` (m4-design §5: "M2
attribution -> injected protocol"), and no concrete adapter over M2's `attribution.pipeline_view`
exists yet -- that view answers a brand-scoped question (a mandatory `brand_id`, and a "touched"
lead count rather than an "attributed" one) that does not line up with this handler's tenant-wide,
`AttributedResults`-shaped call, so adapting it is a documented follow-on rather than something to
approximate here. `_NullAttributionSource` reports zero attributed leads/pipeline in the meantime
-- an honest "RaaS contributes nothing yet" default, consistent with `PricingPlan.raas_enabled`
defaulting to `False` until attribution quality is proven (PRD OQ4).

The freshly computed invoice is persisted in `"draft"` status: this handler closes the *usage
metering* period and prices it, but never sends anything to a customer itself -- reviewing/
finalizing an invoice before it is customer-facing is a separate, deliberately human-gated step
(mirrors the human-in-the-loop posture of `seeding.workflow` elsewhere in M4), not something this
batch job does on its own.
"""

from __future__ import annotations

import logging
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from gw_geo.billing.metering import meter_period
from gw_geo.billing.pricing import (
    AttributedResults,
    AttributionSource,
    PricingPlan,
    compute_invoice,
)
from gw_geo.common.config import get_settings
from gw_geo.common.db import BillingAccount, BillingInvoice

logger = logging.getLogger(__name__)

_DRAFT_STATUS = "draft"

_PlanKind = Literal["starter", "growth", "enterprise"]
_PLAN_KINDS: tuple[_PlanKind, ...] = ("starter", "growth", "enterprise")
_RaasBasis = Literal["per_lead", "pct_pipeline"]
_RAAS_BASES: tuple[_RaasBasis, ...] = ("per_lead", "pct_pipeline")

# A tenant with no `billing_account` row yet (not onboarded to billing): a $0 plan rather than a
# crash, matching this codebase's other graceful-degradation defaults.
_FALLBACK_PLAN = PricingPlan(plan="starter", base_fee=0.0, usage_rates={})


class _NullAttributionSource:
    """Zero-valued `AttributionSource` placeholder for the production (`deps=None`) handler path.

    See this module's docstring: no concrete `AttributionSource` adapter over M2's
    `attribution.pipeline_view` exists yet (a shape/semantics mismatch, not just a missing
    import), so this reports no attributed leads/pipeline rather than approximating one.
    """

    def attributed_results(
        self, *, tenant_id: str, brand_id: str | None, period_start: str, period_end: str
    ) -> AttributedResults:
        logger.warning(
            "billing close tenant_id=%s: no AttributionSource wired yet; reporting 0 "
            "attributed leads/pipeline",
            tenant_id,
        )
        return AttributedResults(attributed_leads=0, attributed_pipeline_usd=0.0)


def _load_plan(session: Session, tenant_id: str) -> PricingPlan:
    """The tenant's `billing_account` row (T02) as a `PricingPlan`, or a $0 `starter` fallback.

    Raises:
        ValueError: the row's `plan`/`raas_basis` is not one of the app-validated values --
            corrupted/out-of-band data must fail loudly here rather than silently reach a caller
            (mirrors `orchestration/retrain.py::_to_pydantic`'s check on `retrain_job.status`).
    """
    account = session.scalar(select(BillingAccount).where(BillingAccount.tenant_id == tenant_id))
    if account is None:
        logger.warning(
            "billing close tenant_id=%s: no billing_account row; using $0 starter fallback plan",
            tenant_id,
        )
        return _FALLBACK_PLAN

    if account.plan not in _PLAN_KINDS:
        raise ValueError(f"billing_account {account.id!r} has unexpected plan {account.plan!r}")
    if account.raas_basis not in _RAAS_BASES:
        raise ValueError(
            f"billing_account {account.id!r} has unexpected raas_basis {account.raas_basis!r}"
        )

    return PricingPlan(
        plan=account.plan,
        base_fee=account.base_fee,
        usage_rates=dict(account.usage_rates),
        raas_enabled=account.raas_enabled,
        raas_basis=account.raas_basis,
        raas_rate=account.raas_rate,
    )


def handler(
    event: dict[str, Any], context: Any = None, *, deps: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Close one billing period for `event["tenant_id"]` (m4-design §4.2/§4.4).

    `event` keys: `tenant_id`, `period_start`, `period_end` (`YYYY-MM-DD`). `context` is the
    Lambda context object, unused here. `deps` (optional): injected `{session, plan, attribution}`
    -- when provided (tests), used verbatim; when omitted (production), `session`/`plan` are built
    from `Settings`/the tenant's `billing_account` row and `attribution` degrades to
    `_NullAttributionSource` (see module docstring).

    Persists a new `BillingInvoice` row (id via `uuid4().hex`) and returns `{"statusCode": 200,
    "body": {"invoice_id": <str>, "total": <float>}}`. Idempotent per `(tenant_id, period_start,
    period_end)`: if an invoice already exists for that period (e.g. a retried monthly cron), the
    existing one is returned unchanged rather than a second draft being inserted.
    """
    tenant_id = event["tenant_id"]
    period_start = event["period_start"]
    period_end = event["period_end"]

    owns_session = deps is None
    session: Session
    plan: PricingPlan
    attribution: AttributionSource
    if deps is not None:
        session = deps["session"]
        plan = deps["plan"]
        attribution = deps["attribution"]
    else:
        settings = get_settings()
        session = Session(create_engine(settings.database_url))
        plan = _load_plan(session, tenant_id)
        attribution = _NullAttributionSource()

    try:
        existing = session.scalar(
            select(BillingInvoice).where(
                BillingInvoice.tenant_id == tenant_id,
                BillingInvoice.period_start == period_start,
                BillingInvoice.period_end == period_end,
            )
        )
        if existing is not None:
            # Idempotent: a retried monthly cron for an already-closed period returns the existing
            # invoice rather than double-inserting a second draft for the same period.
            logger.info(
                "billing close tenant_id=%s period=%s..%s: invoice %s already exists; returning it",
                tenant_id,
                period_start,
                period_end,
                existing.id,
            )
            return {"statusCode": 200, "body": {"invoice_id": existing.id, "total": existing.total}}

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

        invoice_id = uuid4().hex
        session.add(
            BillingInvoice(
                id=invoice_id,
                tenant_id=tenant_id,
                period_start=period_start,
                period_end=period_end,
                base_fee=invoice.base_fee,
                usage_charges=invoice.usage_charges,
                raas_charge=invoice.raas_charge,
                attributed_leads=invoice.attributed_leads,
                attributed_pipeline_usd=invoice.attributed_pipeline_usd,
                total=invoice.total,
                status=_DRAFT_STATUS,
            )
        )
        session.commit()

        return {"statusCode": 200, "body": {"invoice_id": invoice_id, "total": invoice.total}}
    finally:
        if owns_session:
            session.close()
