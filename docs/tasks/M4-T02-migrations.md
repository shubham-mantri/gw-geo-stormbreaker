# M4-T02 — Migrations (seeding · self-adaptation · billing tables)

**Depends on:** M0 db (`common/db.py`, `migrations/`), M1 `drift_event` · **Wave:** 0
**Suggested agent:** general-purpose

**Goal:** SQLAlchemy 2.0 ORM tables + one Alembic migration for all M4 tables (design §2.7, §3, §4.4).
Tenant-scoped tables carry `tenant_id` (FK, indexed); system-level catalogs (`seeding_channel`,
`compliance_rule`, `retrain_job`) are documented exceptions (same rationale as `drift_event`).

**Files:**
- Edit: `src/gw_geo/common/db.py`
- Create: `migrations/versions/0004_m4_seeding_adaptation_billing.py`
- Test: `tests/common/test_db_m4.py`

## Interface

```python
# db.py — new ORM tables (columns per m4-design §2.7/§3/§4.4)
class SeedingChannel(Base):   # system-level catalog
    __tablename__ = "seeding_channel"
    # id, name(unique), source_type, tos_ruleset_ref, requires_disclosure bool,
    # allows_ugc bool, active bool

class ComplianceRule(Base):   # system-level
    __tablename__ = "compliance_rule"
    # id, channel, code, description, severity, check_key, active bool

class SeedingTask(Base):      # tenant-scoped
    __tablename__ = "seeding_task"
    # id, tenant_id(FK,ix), brand_id(FK,ix), content_asset_id null, channel,
    # target_url null, status, compliance_status, compliance_report JSON,
    # brief_ref null, placed_url null, actor null, corroboration_count int,
    # created_at, updated_at

class RetrainJob(Base):       # system-level
    __tablename__ = "retrain_job"
    # id, model_engine, trigger_drift_event_id(FK drift_event.id), status,
    # metrics_before JSON, metrics_after JSON, model_ref null, created_at, completed_at null

class BanditArm(Base):        # tenant-scoped
    __tablename__ = "bandit_arm"
    # id, tenant_id(FK,ix), brand_id(FK,ix), arm_key, pulls int, reward_sum float,
    # reward_sq_sum float, updated_at   (unique per tenant_id+brand_id+arm_key)

class BillingAccount(Base):   # tenant-scoped
    __tablename__ = "billing_account"
    # id, tenant_id(FK,ix), plan, base_fee float, usage_rates JSON, raas_enabled bool,
    # raas_basis, raas_rate float, currency, created_at

class UsageEvent(Base):       # tenant-scoped
    __tablename__ = "usage_event"
    # id, tenant_id(FK,ix), brand_id(FK,ix) null, kind, quantity float, unit, ts, source_ref null

class BillingInvoice(Base):   # tenant-scoped
    __tablename__ = "billing_invoice"
    # id, tenant_id(FK,ix), period_start, period_end, base_fee float, usage_charges JSON,
    # raas_charge float, attributed_leads int, attributed_pipeline_usd float, total float,
    # status, created_at
```

## Steps
- [ ] **1. Failing test** `tests/common/test_db_m4.py` (SQLite in-memory):

```python
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from gw_geo.common.db import (Base, SeedingTask, SeedingChannel, ComplianceRule,
                              RetrainJob, BanditArm, BillingAccount, UsageEvent, BillingInvoice)

def _session():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); return Session(eng)

def test_seeding_task_persists_report_json():
    s = _session()
    s.add(SeedingTask(id="st1", tenant_id="t1", brand_id="b1", channel="reddit",
        status="compliance_review", compliance_status="pending",
        compliance_report={"passed": False, "violations": []}, corroboration_count=0))
    s.commit()
    row = s.scalar(select(SeedingTask).where(SeedingTask.id == "st1"))
    assert row.compliance_report["passed"] is False and row.channel == "reddit"

def test_bandit_arm_and_billing_tables_exist():
    s = _session()
    s.add(BanditArm(id="a1", tenant_id="t1", brand_id="b1", arm_key="reddit:v1",
                    pulls=0, reward_sum=0.0, reward_sq_sum=0.0))
    s.add(BillingAccount(id="acct1", tenant_id="t1", plan="growth", base_fee=500.0,
                         usage_rates={"probe": 0.001}, raas_enabled=False,
                         raas_basis="per_lead", raas_rate=0.0, currency="USD"))
    s.commit()
    assert s.scalar(select(BanditArm).where(BanditArm.arm_key == "reddit:v1")).pulls == 0
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** the ORM tables (use `String`/`JSON`/`Float`/`Integer`/`Boolean`/`DateTime`);
  `tenant_id`/`brand_id` indexed FKs on tenant-scoped tables; unique index on
  `bandit_arm(tenant_id, brand_id, arm_key)` and `seeding_channel(name)`. Author
  `migrations/versions/0004_m4_seeding_adaptation_billing.py` with `down_revision` = the latest
  existing head (M1's migration); `upgrade()` creates all tables, `downgrade()` drops them.
- [ ] **4. Run → pass**; verify `alembic upgrade head` runs clean on a scratch SQLite/PG url;
  `mypy src/gw_geo/common` clean.
- [ ] **5. Commit:** `feat(db): M4 seeding, self-adaptation & billing tables + migration`

## Acceptance
- All M4 tables created via ORM + Alembic; JSON columns round-trip; tenant-scoped tables carry
  indexed `tenant_id`; system-level tables documented; `alembic upgrade head` clean; tests green on SQLite.
