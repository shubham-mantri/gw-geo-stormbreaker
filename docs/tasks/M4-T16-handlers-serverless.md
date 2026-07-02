# M4-T16 — Handlers + serverless wiring (adaptation cron · billing close)

**Depends on:** T13 (billing views), T15 (scheduler) · **Wave:** 3
**Suggested agent:** general-purpose (integration task)

**Goal:** Expose the M4 loop as scheduled Lambda handlers and register them in `serverless.yml`
(mirrors M0's `handlers/run_measurement.py` + M1's drift schedule): an **adaptation-cycle** handler
(EventBridge cron → `run_adaptation_cycle`) and a **billing period-close** handler (monthly cron →
persist `billing_invoice`). Handlers build their real collaborators via a wiring/factory; tests invoke
handlers with **injected fakes and `moto`** — no live AWS/network/posting.

**Files:**
- Create: `src/gw_geo/handlers/run_adaptation.py`, `src/gw_geo/handlers/close_billing.py`
- Edit: `serverless.yml`
- Test: `tests/handlers/test_adaptation_handler.py`, `tests/handlers/test_billing_handler.py`

## Interface

```python
# handlers/run_adaptation.py
def handler(event: dict, context=None, *, deps=None) -> dict: ...
#   event: {"tenant_id","brand_id","since","until","budget","date"}
#   deps: optional injected {drift_runner, retrain_trigger, discovery, workflow, bandit_policy, session}
#   -> {"statusCode":200, "body": CycleResult-as-dict}

# handlers/close_billing.py
def handler(event: dict, context=None, *, deps=None) -> dict: ...
#   event: {"tenant_id","period_start","period_end"}
#   deps: optional injected {session, plan, attribution}
#   persists a BillingInvoice row -> {"statusCode":200, "body": {"invoice_id","total"}}
```

`deps` injection keeps handlers hermetic: production builds real deps from `Settings`/wiring; tests
pass fakes. Add two `functions:` entries in `serverless.yml` with `schedule` events (cron) — not HTTP.

## Steps
- [ ] **1. Failing test** `tests/handlers/test_adaptation_handler.py`:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from gw_geo.common.db import Base
from gw_geo.handlers.run_adaptation import handler

class FakeRetrain:
    def poll(self): return []

def test_adaptation_handler_runs_with_injected_deps():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); s = Session(eng)
    deps = {"drift_runner": (lambda: []), "retrain_trigger": FakeRetrain(),
            "discovery": (lambda: []),
            "workflow": type("W", (), {"create": lambda self, **k: "st0"})(),
            "bandit_policy": None, "session": s}
    out = handler({"tenant_id": "t1", "brand_id": "b1", "since": "a", "until": "b",
                   "budget": 2, "date": "2026-07-02"}, deps=deps)
    assert out["statusCode"] == 200
    assert out["body"]["targets_found"] == 0
```

```python
# tests/handlers/test_billing_handler.py
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from gw_geo.common.db import Base, BillingInvoice
from gw_geo.billing.pricing import PricingPlan, AttributedResults
from gw_geo.handlers.close_billing import handler

class FakeAttribution:
    def attributed_results(self, *, tenant_id, brand_id, period_start, period_end):
        return AttributedResults(attributed_leads=10, attributed_pipeline_usd=0.0)

def test_billing_handler_persists_invoice():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); s = Session(eng)
    plan = PricingPlan(plan="growth", base_fee=500.0, usage_rates={})
    out = handler({"tenant_id": "t1", "period_start": "2026-06-01", "period_end": "2026-07-01"},
                  deps={"session": s, "plan": plan, "attribution": FakeAttribution()})
    assert out["statusCode"] == 200
    assert s.query(BillingInvoice).count() == 1 and out["body"]["total"] == 500.0
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** both handlers (default deps built from wiring; injectable for tests), persist the
  invoice, and add the two scheduled `functions` to `serverless.yml`. No live AWS in the default suite.
- [ ] **4. Run → pass**; `ruff`/`mypy` clean.
- [ ] **5. Commit:** `feat(handlers): adaptation-cycle + billing-close Lambdas + serverless wiring`

## Acceptance
- Both handlers run hermetically with injected deps; adaptation handler returns a `CycleResult` body;
  billing handler persists a `BillingInvoice`; `serverless.yml` gains two cron-scheduled functions; no
  live AWS/network/posting.
