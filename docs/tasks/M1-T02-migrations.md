# M1-T02 — Migrations: `drift_event` + `visibility_rollup`

**Depends on:** M0-T04 (db + alembic) · **Wave:** 0 · **Suggested agent:** general-purpose

**Goal:** Add the two new M1 tables — `drift_event` (system-level engine drift) and
`visibility_rollup` (daily tenant-scoped rollup for fast dashboard time-series) — as SQLAlchemy ORM
models plus an Alembic migration (m1-design §6).

**Files:**
- Modify: `src/gw_geo/common/db.py`
- Create: `migrations/versions/0002_m1_drift_and_rollup.py`
- Test: `tests/common/test_db.py` (add cases)

## Interface

Add two ORM tables to `db.py` (SQLAlchemy 2.0, `Base` from M0-T04):

```python
class DriftEvent(Base):
    __tablename__ = "drift_event"
    # SYSTEM-LEVEL: no tenant_id — engine drift is global (documented exception to the
    # per-row tenant_id rule; m1-design §6).
    id: Mapped[str] = mapped_column(String, primary_key=True)
    engine: Mapped[str] = mapped_column(String, index=True)
    canary_id: Mapped[str] = mapped_column(String, index=True)
    baseline_rate: Mapped[float] = mapped_column(Float)
    observed_rate: Mapped[float] = mapped_column(Float)
    drop: Mapped[float] = mapped_column(Float)
    breached: Mapped[bool] = mapped_column(Boolean)
    retrain_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    ts: Mapped[datetime] = mapped_column(DateTime)

class VisibilityRollup(Base):
    __tablename__ = "visibility_rollup"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenant.id"), index=True)
    brand_id: Mapped[str] = mapped_column(String, index=True)
    engine: Mapped[str] = mapped_column(String, index=True)
    geo: Mapped[str] = mapped_column(String)
    persona: Mapped[str | None] = mapped_column(String, nullable=True)
    date: Mapped[str] = mapped_column(String, index=True)
    mention_rate: Mapped[float] = mapped_column(Float)
    citation_rate: Mapped[float] = mapped_column(Float)
    avg_position: Mapped[float | None] = mapped_column(Float, nullable=True)
    sentiment_score: Mapped[float] = mapped_column(Float)
    share_of_voice: Mapped[float] = mapped_column(Float)
    n_samples: Mapped[int] = mapped_column(Integer)
```

`visibility_rollup` is **tenant-scoped** (visible via `TenantScopedSession`); `drift_event` is
intentionally system-level. The Alembic `0002` migration creates both tables (upgrade) and drops
them (downgrade), chained on top of `0001_init`.

## Steps
- [ ] **1. Failing test** — add to `tests/common/test_db.py`:

```python
from datetime import datetime
from gw_geo.common.db import Base, DriftEvent, VisibilityRollup

def test_drift_event_is_system_level():
    assert "tenant_id" not in DriftEvent.__table__.columns  # global, by design
    s = _session()
    s.add(DriftEvent(id="d1", engine="gemini", canary_id="c1", baseline_rate=0.8,
        observed_rate=0.5, drop=0.3, breached=True, retrain_flag=True, ts=datetime.utcnow()))
    s.commit()
    assert s.get(DriftEvent, "d1").breached is True

def test_visibility_rollup_roundtrips():
    s = _session()
    s.add(VisibilityRollup(id="r1", tenant_id="t1", brand_id="b1", engine="gemini",
        geo="us", persona=None, date="2026-07-02", mention_rate=0.4, citation_rate=0.2,
        avg_position=2.0, sentiment_score=0.5, share_of_voice=0.3, n_samples=12))
    s.commit()
    assert s.get(VisibilityRollup, "r1").n_samples == 12
```

- [ ] **2. Run → fail.** `pytest tests/common/test_db.py -v`
- [ ] **3. Implement** the two ORM tables in `db.py`; author `migrations/versions/0002_m1_drift_and_rollup.py`
  (`down_revision = "0001"`) creating/dropping both tables. Verify `alembic upgrade head` +
  `downgrade` runs against a scratch SQLite/Postgres URL.
- [ ] **4. Run → pass**; `mypy src/gw_geo/common` clean.
- [ ] **5. Commit:** `feat(common): drift_event + visibility_rollup tables (alembic 0002)`

## Acceptance
- Both tables exist as ORM models with exact columns; Alembic `0002` up/down works chained on
  `0001`; `drift_event` has no `tenant_id` (documented system-level exception); `visibility_rollup`
  is tenant-scoped; tests green on SQLite.
