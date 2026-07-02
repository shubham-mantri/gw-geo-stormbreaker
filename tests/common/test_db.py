from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.db import Base, Brand, TenantScopedSession


def _session():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); return Session(eng)  # noqa: E702 -- verbatim per task spec


def test_scope_blocks_cross_tenant():
    s = _session()
    s.add(Brand(id="b1", tenant_id="t1", name="A", domain="a.com")); s.commit()  # noqa: E702 -- verbatim per task spec
    scoped = TenantScopedSession(s, tenant_id="t2")
    assert scoped.query_brands().all() == []


def test_add_rejects_foreign_tenant():
    scoped = TenantScopedSession(_session(), tenant_id="t1")
    import pytest
    with pytest.raises(ValueError):
        scoped.add(Brand(id="b2", tenant_id="t2", name="B", domain="b.com"))
