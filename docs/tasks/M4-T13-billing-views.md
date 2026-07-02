# M4-T13 — Billing views (RaaS/billing dashboard queries)

**Depends on:** T08 (metering), T09 (pricing) · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** The read/query layer backing the Settings → billing screen (ui-spec §3.8, §7 M4 RaaS/billing
views; design §4.3): current-period running total, usage breakdown, RaaS contribution, and invoice
history. Composes T08 metering + T09 pricing + an injected `AttributionSource` (M2), all decoupled.

**Files:**
- Create: `src/gw_geo/billing/views.py`
- Test: `tests/billing/test_views.py`

## Interface (design §4.3)

```python
from typing import Any
from gw_geo.billing.pricing import PricingPlan, AttributionSource

def billing_summary(session, *, tenant_id: str, plan: PricingPlan,
                    attribution: AttributionSource, period_start: str,
                    period_end: str) -> dict[str, Any]: ...
#   -> {base_fee, usage_charges:{kind:amt}, raas_charge, attributed_leads,
#       attributed_pipeline_usd, total, currency, period_start, period_end}

def invoice_history(session, *, tenant_id: str, limit: int = 12) -> list[dict[str, Any]]: ...
#   -> [{period_start, period_end, total, raas_charge, status}, ...] newest first
```

`billing_summary` meters the period (T08), pulls attributed results via the injected
`attribution.attributed_results(...)`, computes the invoice (T09), and returns a dashboard-ready dict.
`invoice_history` reads persisted `billing_invoice` rows, tenant-scoped, newest first.

## Steps
- [ ] **1. Failing test** `tests/billing/test_views.py` (SQLite):

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from gw_geo.common.db import Base, BillingInvoice
from gw_geo.billing.metering import UsageKind, record_usage
from gw_geo.billing.pricing import PricingPlan, AttributedResults
from gw_geo.billing.views import billing_summary, invoice_history

class FakeAttribution:
    def attributed_results(self, *, tenant_id, brand_id, period_start, period_end):
        return AttributedResults(attributed_leads=100, attributed_pipeline_usd=300000.0)

def _session():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); return Session(eng)

def test_billing_summary_composes_usage_and_raas():
    s = _session()
    record_usage(s, tenant_id="t1", brand_id="b1", kind=UsageKind.PROBE,
                 quantity=1000, ts="2026-06-10"); s.commit()
    plan = PricingPlan(plan="enterprise", base_fee=1000.0, usage_rates={"probe": 0.001},
                       raas_enabled=True, raas_basis="per_lead", raas_rate=20.0)
    out = billing_summary(s, tenant_id="t1", plan=plan, attribution=FakeAttribution(),
                          period_start="2026-06-01", period_end="2026-07-01")
    assert out["usage_charges"]["probe"] == 1.0
    assert out["raas_charge"] == 100 * 20.0
    assert out["total"] == 1000.0 + 1.0 + 2000.0 and out["attributed_leads"] == 100

def test_invoice_history_newest_first():
    s = _session()
    s.add(BillingInvoice(id="i1", tenant_id="t1", period_start="2026-05-01",
        period_end="2026-06-01", base_fee=1000.0, usage_charges={}, raas_charge=0.0,
        attributed_leads=0, attributed_pipeline_usd=0.0, total=1000.0, status="paid"))
    s.add(BillingInvoice(id="i2", tenant_id="t1", period_start="2026-06-01",
        period_end="2026-07-01", base_fee=1000.0, usage_charges={}, raas_charge=500.0,
        attributed_leads=25, attributed_pipeline_usd=0.0, total=1500.0, status="open"))
    s.commit()
    hist = invoice_history(s, tenant_id="t1")
    assert [h["period_start"] for h in hist] == ["2026-06-01", "2026-05-01"]
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `billing_summary` (meter → attribution → `compute_invoice` → dict) and
  `invoice_history` (tenant-scoped, order by `period_start` desc, `limit`).
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(billing): billing summary + invoice history views`

## Acceptance
- `billing_summary` returns a dashboard-ready dict combining usage + RaaS via the injected attribution;
  `invoice_history` returns tenant-scoped invoices newest-first; hermetic.
