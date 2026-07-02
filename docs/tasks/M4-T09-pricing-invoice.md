# M4-T09 — RaaS pricing model + invoice computation

**Depends on:** M0 models (consumes an injected `AttributionSource`) · **Wave:** 1
**Suggested agent:** general-purpose

**Goal:** The pricing math: base fee + per-unit usage rates + optional **RaaS** charge on **attributed
leads/pipeline** (design §4.2, PRD §9). Pure, deterministic, heavily unit-tested. Attribution is an
**injected `AttributionSource` protocol** (satisfied by M2), so billing builds/tests before M2 lands.

**Files:**
- Create: `src/gw_geo/billing/pricing.py`
- Test: `tests/billing/test_pricing.py`

## Interface (design §4.2)

```python
from typing import Literal, Protocol
from pydantic import BaseModel
from gw_geo.billing.metering import UsageSummary

class PricingPlan(BaseModel):
    plan: Literal["starter", "growth", "enterprise"]
    base_fee: float
    usage_rates: dict[str, float]                       # UsageKind → $/unit
    raas_enabled: bool = False
    raas_basis: Literal["per_lead", "pct_pipeline"] = "per_lead"
    raas_rate: float = 0.0

class AttributedResults(BaseModel):
    attributed_leads: int
    attributed_pipeline_usd: float

class AttributionSource(Protocol):                      # satisfied by M2 attribution
    def attributed_results(self, *, tenant_id: str, brand_id: str | None,
                           period_start: str, period_end: str) -> AttributedResults: ...

class Invoice(BaseModel):
    tenant_id: str; period_start: str; period_end: str
    base_fee: float; usage_charges: dict[str, float]
    raas_charge: float; attributed_leads: int; attributed_pipeline_usd: float
    total: float; currency: str = "USD"

def compute_invoice(*, tenant_id: str, plan: PricingPlan, usage: UsageSummary,
                    results: AttributedResults, period_start: str,
                    period_end: str) -> Invoice: ...
```

`usage_charges[kind] = usage.by_kind[kind] * plan.usage_rates.get(kind, 0)`. RaaS charge = 0 when
`raas_enabled` is False; else `attributed_leads * raas_rate` (`per_lead`) or
`attributed_pipeline_usd * raas_rate` (`pct_pipeline`). `total = base_fee + sum(usage_charges) + raas_charge`.

## Steps
- [ ] **1. Failing test** `tests/billing/test_pricing.py`:

```python
from gw_geo.billing.metering import UsageSummary
from gw_geo.billing.pricing import PricingPlan, AttributedResults, compute_invoice

def _usage(): return UsageSummary(tenant_id="t1", period_start="2026-06-01",
    period_end="2026-07-01", by_kind={"probe": 1000.0, "seeding_placement": 4.0})

def test_usage_only_when_raas_disabled():
    plan = PricingPlan(plan="growth", base_fee=500.0,
                       usage_rates={"probe": 0.001, "seeding_placement": 50.0}, raas_enabled=False)
    inv = compute_invoice(tenant_id="t1", plan=plan, usage=_usage(),
        results=AttributedResults(attributed_leads=137, attributed_pipeline_usd=480000.0),
        period_start="2026-06-01", period_end="2026-07-01")
    assert inv.raas_charge == 0.0
    assert inv.usage_charges["probe"] == 1.0 and inv.usage_charges["seeding_placement"] == 200.0
    assert inv.total == 500.0 + 1.0 + 200.0

def test_raas_per_lead():
    plan = PricingPlan(plan="enterprise", base_fee=2000.0, usage_rates={"probe": 0.001},
                       raas_enabled=True, raas_basis="per_lead", raas_rate=25.0)
    inv = compute_invoice(tenant_id="t1", plan=plan, usage=_usage(),
        results=AttributedResults(attributed_leads=137, attributed_pipeline_usd=480000.0),
        period_start="2026-06-01", period_end="2026-07-01")
    assert inv.raas_charge == 137 * 25.0

def test_raas_pct_pipeline():
    plan = PricingPlan(plan="enterprise", base_fee=0.0, usage_rates={},
                       raas_enabled=True, raas_basis="pct_pipeline", raas_rate=0.02)
    inv = compute_invoice(tenant_id="t1", plan=plan,
        usage=UsageSummary(tenant_id="t1", period_start="a", period_end="b", by_kind={}),
        results=AttributedResults(attributed_leads=0, attributed_pipeline_usd=480000.0),
        period_start="a", period_end="b")
    assert inv.raas_charge == 480000.0 * 0.02 and inv.total == inv.raas_charge
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `compute_invoice` — pure math per the spec; no I/O. `AttributionSource` is only
  a type for callers (T13); this function takes `results` directly for testability.
- [ ] **4. Run → pass**; add a property test: `total` is monotonic non-decreasing in each usage
  quantity and in `attributed_leads` when RaaS `per_lead` is on.
- [ ] **5. Commit:** `feat(billing): RaaS pricing + invoice computation`

## Acceptance
- Usage charges = quantity×rate per kind; RaaS off ⇒ zero RaaS charge; `per_lead` and `pct_pipeline`
  both correct; total = base + usage + RaaS; pure/deterministic.
