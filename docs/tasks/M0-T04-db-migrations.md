# M0-T04 — DB layer, schema & tenant scoping

**Depends on:** T02 · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** SQLAlchemy 2.0 tables for TRD §4, Alembic migration, and a `TenantScopedSession` that
prevents cross-tenant reads.

**Files:**
- Create: `src/gw_geo/common/db.py`, `alembic.ini`, `migrations/env.py`,
  `migrations/versions/0001_init.py`
- Test: `tests/common/test_db.py`

## Interface

```python
# db.py
from sqlalchemy.orm import DeclarativeBase, Session

class Base(DeclarativeBase): ...
# ORM tables: Tenant, Brand, Prompt, ProbeRun, AnswerExtraction, Citation, VisibilitySnapshot
# — columns per TRD §4, every table (except Tenant) has tenant_id (FK, indexed).

class TenantScopedSession:
    def __init__(self, session: Session, tenant_id: str) -> None: ...
    def query_brands(self): ...          # auto-filters tenant_id
    def add(self, obj) -> None: ...       # asserts obj.tenant_id == self.tenant_id
    def commit(self) -> None: ...
```

## Steps
- [ ] **1. Failing test** `tests/common/test_db.py` (SQLite in-memory for unit speed):

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from gw_geo.common.db import Base, Brand, TenantScopedSession

def _session():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); return Session(eng)

def test_scope_blocks_cross_tenant():
    s = _session()
    s.add(Brand(id="b1", tenant_id="t1", name="A", domain="a.com")); s.commit()
    scoped = TenantScopedSession(s, tenant_id="t2")
    assert scoped.query_brands().all() == []

def test_add_rejects_foreign_tenant():
    scoped = TenantScopedSession(_session(), tenant_id="t1")
    import pytest
    with pytest.raises(ValueError):
        scoped.add(Brand(id="b2", tenant_id="t2", name="B", domain="b.com"))
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** ORM tables + `TenantScopedSession`. Use `String`/`JSON`/`Float`/`Integer`
  columns; `tenant_id` indexed FK. Generate the Alembic `0001_init` from `Base.metadata`.
- [ ] **4. Run → pass**; `mypy src/gw_geo/common` clean.
- [ ] **5. Commit:** `feat(common): db schema + tenant-scoped session + alembic init`

## Acceptance
- All TRD §4 tables created via Alembic; `TenantScopedSession` filters reads and rejects
  cross-tenant writes; tests green on SQLite.
