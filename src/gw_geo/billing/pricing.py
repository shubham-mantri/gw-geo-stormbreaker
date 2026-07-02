"""RaaS pricing model + invoice computation (m4-design §4.2, PRD §9): base fee + per-unit usage
rates + an optional results-linked (RaaS) charge on **attributed** leads/pipeline.

Attribution is consumed via an injected `AttributionSource` protocol, satisfied by M2's
attribution subsystem, so this module is decoupled from -- and fully unit-tested before -- M2:
`compute_invoice` takes the attributed `results` (`AttributedResults`) directly rather than
calling an `AttributionSource` itself. `AttributionSource` exists here purely as the type callers
(`billing/views.py`, T13) use to resolve those results before invoking `compute_invoice`.

`compute_invoice` is pure, deterministic math -- no I/O, no side effects.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

from gw_geo.billing.metering import UsageSummary


class PricingPlan(BaseModel):
    """A tenant's billing plan (m4-design §4.2/§4.4): a flat `base_fee` plus `usage_rates`
    (`UsageKind` value -> $/unit) and an optional RaaS charge on attributed results. RaaS
    defaults to disabled (PRD OQ4) until attribution quality is proven.
    """

    plan: Literal["starter", "growth", "enterprise"]
    base_fee: float
    usage_rates: dict[str, float] = Field(default_factory=dict)
    raas_enabled: bool = False
    raas_basis: Literal["per_lead", "pct_pipeline"] = "per_lead"
    raas_rate: float = 0.0


class AttributedResults(BaseModel):
    """Attributed leads/pipeline for a tenant over a billing period -- the numbers the RaaS
    charge is computed on. Produced by an `AttributionSource` (M2) and passed to
    `compute_invoice` directly, so this module never depends on M2 at import or test time.
    """

    attributed_leads: int
    attributed_pipeline_usd: float


class AttributionSource(Protocol):
    """Injectable source of attributed results, satisfied by M2's attribution subsystem.

    `compute_invoice` itself takes `AttributedResults` directly (for testability without M2);
    this protocol is only the type callers (`billing/views.py`, T13) use to resolve those
    results before calling `compute_invoice`.
    """

    def attributed_results(
        self, *, tenant_id: str, brand_id: str | None, period_start: str, period_end: str
    ) -> AttributedResults: ...


class Invoice(BaseModel):
    """One computed invoice for a billing period (m4-design §4.2/§4.4) -- the pure return value
    of `compute_invoice`; the persisted form is `common.db.BillingInvoice`.
    """

    tenant_id: str
    period_start: str
    period_end: str
    base_fee: float
    usage_charges: dict[str, float]
    raas_charge: float
    attributed_leads: int
    attributed_pipeline_usd: float
    total: float
    currency: str = "USD"


def compute_invoice(
    *,
    tenant_id: str,
    plan: PricingPlan,
    usage: UsageSummary,
    results: AttributedResults,
    period_start: str,
    period_end: str,
) -> Invoice:
    """Pure pricing math (m4-design §4.2): no I/O, fully deterministic.

    `usage_charges[kind] = usage.by_kind[kind] * plan.usage_rates.get(kind, 0)` for every kind
    present in `usage.by_kind`. The RaaS charge is zero unless `plan.raas_enabled`; otherwise it
    is `attributed_leads * raas_rate` under the `per_lead` basis, or `attributed_pipeline_usd *
    raas_rate` under `pct_pipeline`. `total` sums `base_fee`, every usage charge, and the RaaS
    charge.
    """
    usage_charges = {
        kind: quantity * plan.usage_rates.get(kind, 0.0)
        for kind, quantity in usage.by_kind.items()
    }

    if not plan.raas_enabled:
        raas_charge = 0.0
    elif plan.raas_basis == "per_lead":
        raas_charge = results.attributed_leads * plan.raas_rate
    else:
        raas_charge = results.attributed_pipeline_usd * plan.raas_rate

    total = plan.base_fee + sum(usage_charges.values()) + raas_charge

    return Invoice(
        tenant_id=tenant_id,
        period_start=period_start,
        period_end=period_end,
        base_fee=plan.base_fee,
        usage_charges=usage_charges,
        raas_charge=raas_charge,
        attributed_leads=results.attributed_leads,
        attributed_pipeline_usd=results.attributed_pipeline_usd,
        total=total,
    )
