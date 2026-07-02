"""Tests for RaaS pricing + invoice computation (docs/tasks/M4-T09-pricing-invoice.md,
m4-design §4.2): usage charges are quantity x rate per kind; the RaaS charge is zero unless
`raas_enabled`, else `attributed_leads * raas_rate` (`per_lead`) or
`attributed_pipeline_usd * raas_rate` (`pct_pipeline`); `total` sums base fee, usage charges, and
the RaaS charge.
"""

from gw_geo.billing.metering import UsageSummary
from gw_geo.billing.pricing import AttributedResults, PricingPlan, compute_invoice


def _usage() -> UsageSummary:
    return UsageSummary(
        tenant_id="t1",
        period_start="2026-06-01",
        period_end="2026-07-01",
        by_kind={"probe": 1000.0, "seeding_placement": 4.0},
    )


def test_usage_only_when_raas_disabled() -> None:
    plan = PricingPlan(
        plan="growth",
        base_fee=500.0,
        usage_rates={"probe": 0.001, "seeding_placement": 50.0},
        raas_enabled=False,
    )
    inv = compute_invoice(
        tenant_id="t1",
        plan=plan,
        usage=_usage(),
        results=AttributedResults(attributed_leads=137, attributed_pipeline_usd=480000.0),
        period_start="2026-06-01",
        period_end="2026-07-01",
    )
    assert inv.raas_charge == 0.0
    assert inv.usage_charges["probe"] == 1.0 and inv.usage_charges["seeding_placement"] == 200.0
    assert inv.total == 500.0 + 1.0 + 200.0


def test_raas_per_lead() -> None:
    plan = PricingPlan(
        plan="enterprise",
        base_fee=2000.0,
        usage_rates={"probe": 0.001},
        raas_enabled=True,
        raas_basis="per_lead",
        raas_rate=25.0,
    )
    inv = compute_invoice(
        tenant_id="t1",
        plan=plan,
        usage=_usage(),
        results=AttributedResults(attributed_leads=137, attributed_pipeline_usd=480000.0),
        period_start="2026-06-01",
        period_end="2026-07-01",
    )
    assert inv.raas_charge == 137 * 25.0


def test_raas_pct_pipeline() -> None:
    plan = PricingPlan(
        plan="enterprise",
        base_fee=0.0,
        usage_rates={},
        raas_enabled=True,
        raas_basis="pct_pipeline",
        raas_rate=0.02,
    )
    inv = compute_invoice(
        tenant_id="t1",
        plan=plan,
        usage=UsageSummary(tenant_id="t1", period_start="a", period_end="b", by_kind={}),
        results=AttributedResults(attributed_leads=0, attributed_pipeline_usd=480000.0),
        period_start="a",
        period_end="b",
    )
    assert inv.raas_charge == 480000.0 * 0.02 and inv.total == inv.raas_charge


def test_total_monotonic_in_usage_and_attributed_leads() -> None:
    """Property (spec step 4): holding everything else fixed, `total` is monotonic
    non-decreasing in each usage quantity, and in `attributed_leads` when RaaS `per_lead`
    is on.
    """
    plan = PricingPlan(
        plan="enterprise",
        base_fee=1000.0,
        usage_rates={"probe": 0.001, "seeding_placement": 50.0},
        raas_enabled=True,
        raas_basis="per_lead",
        raas_rate=25.0,
    )
    fixed_results = AttributedResults(attributed_leads=100, attributed_pipeline_usd=250000.0)

    for varying_kind in ("probe", "seeding_placement"):
        prev_total = float("-inf")
        for quantity in range(0, 1001, 100):
            by_kind = {"probe": 500.0, "seeding_placement": 10.0}
            by_kind[varying_kind] = float(quantity)
            usage = UsageSummary(
                tenant_id="t1", period_start="2026-06-01", period_end="2026-07-01",
                by_kind=by_kind,
            )
            inv = compute_invoice(
                tenant_id="t1",
                plan=plan,
                usage=usage,
                results=fixed_results,
                period_start="2026-06-01",
                period_end="2026-07-01",
            )
            assert inv.total >= prev_total
            prev_total = inv.total

    fixed_usage = UsageSummary(
        tenant_id="t1",
        period_start="2026-06-01",
        period_end="2026-07-01",
        by_kind={"probe": 500.0, "seeding_placement": 10.0},
    )
    prev_total = float("-inf")
    for attributed_leads in range(0, 501, 50):
        inv = compute_invoice(
            tenant_id="t1",
            plan=plan,
            usage=fixed_usage,
            results=AttributedResults(
                attributed_leads=attributed_leads, attributed_pipeline_usd=250000.0
            ),
            period_start="2026-06-01",
            period_end="2026-07-01",
        )
        assert inv.total >= prev_total
        prev_total = inv.total
