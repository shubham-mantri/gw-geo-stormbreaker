# M2-T02 — Migrations & ORM (attribution + auth tables)

**Depends on:** M0 db (T04) · **Wave:** 0 · **Suggested agent:** general-purpose

**Goal:** Add the M2 tables (m2-design §8) as SQLAlchemy 2.0 ORM models + one Alembic migration, all
**tenant-scoped** (`tenant_id` FK, indexed) except the auth tables noted below. `TenantScopedSession`
(M0 T04) must transparently scope the new tables.

**Files:**
- Edit: `src/gw_geo/common/db.py`
- Create: `migrations/versions/0002_m2_attribution_auth.py`
- Test: `tests/common/test_db_m2.py`

## Interface (ORM tables — columns per m2-design §8)

```python
# session, lead, attribution_link, holdout_cohort, integration — all carry tenant_id (FK, indexed)
class Session(Base):            # id, tenant_id, brand_id, visitor_id, landing_url, referrer,
    __tablename__ = "session"   # utm(JSON), engine(nullable), user_agent, ts
class Lead(Base):               # id, tenant_id, brand_id, visitor_id, session_id, email,
    __tablename__ = "lead"      # value_usd, crm_stage, self_reported_source, ts
class AttributionLink(Base):    # id, tenant_id, brand_id, lead_id, session_id, citation_id(null),
    __tablename__ = "attribution_link"  # prompt_id(null), engine, method, confidence, value_usd, ts
class HoldoutCohort(Base):      # id, tenant_id, brand_id, name, kind, prompt_ids(JSON), geo,
    __tablename__ = "holdout_cohort"    # is_holdout(bool), started_at
class Integration(Base):        # id, tenant_id, kind, status, config_ref, connected_at
    __tablename__ = "integration"
# auth tables (app_user is NOT tenant-scoped; membership maps user↔tenant↔role)
class AppUser(Base):            # id, email(unique), password_hash, created_at
    __tablename__ = "app_user"
class Membership(Base):         # id, user_id(FK app_user), tenant_id(FK tenant), role
    __tablename__ = "membership"
```
`method` ∈ `direct|citation_linked|assisted|holdout_incremental`;
`confidence` ∈ `high|medium|reported|modeled|low`; `role` ∈ `owner|admin|editor|viewer`.

## Steps
- [ ] **1. Failing test** `tests/common/test_db_m2.py` (SQLite in-memory):

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SASession
from gw_geo.common.db import Base, Lead, Membership, TenantScopedSession

def _s():
    e = create_engine("sqlite://"); Base.metadata.create_all(e); return SASession(e)

def test_lead_is_tenant_scoped():
    s = _s()
    s.add(Lead(id="l1", tenant_id="t1", brand_id="b1", visitor_id="v1", email="a@x.com"))
    s.commit()
    assert TenantScopedSession(s, "t2").query(Lead).all() == []
    assert len(TenantScopedSession(s, "t1").query(Lead).all()) == 1

def test_membership_maps_user_to_role():
    s = _s()
    s.add(Membership(id="m1", user_id="u1", tenant_id="t1", role="editor")); s.commit()
    m = s.get(Membership, "m1")
    assert m.role == "editor" and m.tenant_id == "t1"
```
(If M0's `TenantScopedSession` exposes `query_<table>` helpers instead of a generic `query`, add the
matching scoped accessors for the new tables and adjust the test to them.)

- [ ] **2. Run → fail.**
- [ ] **3. Implement** ORM tables; extend `TenantScopedSession` to scope the new tenant tables; write
  `0002_m2_attribution_auth` migration (generate from `Base.metadata`, verify `alembic upgrade head`
  on a scratch SQLite/Postgres). `app_user` has no `tenant_id` (documented exception, like M1
  `drift_event`).
- [ ] **4. Run → pass**; `mypy src/gw_geo/common` clean.
- [ ] **5. Commit:** `feat(common): M2 attribution + auth tables + alembic 0002`

## Acceptance
- All 7 tables created via Alembic; tenant tables auto-scope through `TenantScopedSession`;
  `app_user`/`membership` model the auth join; enum-like columns accept only the documented values
  (checked in app layer); tests green on SQLite.
