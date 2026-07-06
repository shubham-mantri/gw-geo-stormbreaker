from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from gw_geo.common.db import Base, Brand, DriftEvent, Tenant, TenantScopedSession, VisibilityRollup


def _session():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); return Session(eng)  # noqa: E702 -- verbatim per task spec


def test_scope_blocks_cross_tenant():
    s = _session()
    s.add(Tenant(id="t1", name="A", sampling_budget_daily=100.0))
    s.add(Brand(id="b1", tenant_id="t1", name="A", domain="a.com")); s.commit()  # noqa: E702 -- verbatim per task spec
    scoped = TenantScopedSession(s, tenant_id="t2")
    assert scoped.query_brands().all() == []


def test_add_rejects_foreign_tenant():
    scoped = TenantScopedSession(_session(), tenant_id="t1")
    with pytest.raises(ValueError):
        scoped.add(Brand(id="b2", tenant_id="t2", name="B", domain="b.com"))


def test_orphan_child_insert_raises():
    """A child insert whose FK parent is absent must be rejected at commit time.

    This proves the conftest `connect` listener's `PRAGMA foreign_keys=ON` is in force: with FK
    enforcement OFF (SQLite's default, the bug this hardening fixes) SQLite silently accepts the
    orphan; with it ON, SQLite behaves like Postgres and raises. Exercises `Brand.tenant_id` ->
    `tenant.id`, seeding no `Tenant`.
    """
    s = _session()
    s.add(Brand(id="orphan", tenant_id="ghost-tenant", name="X", domain="x.com"))
    with pytest.raises(IntegrityError):
        s.commit()


def test_drift_event_is_system_level():
    assert "tenant_id" not in DriftEvent.__table__.columns  # global, by design
    s = _session()
    s.add(DriftEvent(id="d1", engine="gemini", canary_id="c1", baseline_rate=0.8,
        observed_rate=0.5, drop=0.3, breached=True, retrain_flag=True, ts=datetime.utcnow()))
    s.commit()
    assert s.get(DriftEvent, "d1").breached is True


def test_visibility_rollup_roundtrips():
    s = _session()
    s.add(Tenant(id="t1", name="A", sampling_budget_daily=100.0))
    s.add(VisibilityRollup(id="r1", tenant_id="t1", brand_id="b1", engine="gemini",
        geo="us", persona=None, date="2026-07-02", mention_rate=0.4, citation_rate=0.2,
        avg_position=2.0, sentiment_score=0.5, share_of_voice=0.3, n_samples=12))
    s.commit()
    assert s.get(VisibilityRollup, "r1").n_samples == 12
