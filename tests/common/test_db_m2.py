from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SASession

from gw_geo.common.db import Base, Lead, Membership, TenantScopedSession


def _s() -> SASession:
    e = create_engine("sqlite://")
    Base.metadata.create_all(e)
    return SASession(e)


def test_lead_is_tenant_scoped() -> None:
    s = _s()
    s.add(Lead(id="l1", tenant_id="t1", brand_id="b1", visitor_id="v1", email="a@x.com"))
    s.commit()
    assert TenantScopedSession(s, "t2").query(Lead).all() == []
    assert len(TenantScopedSession(s, "t1").query(Lead).all()) == 1


def test_membership_maps_user_to_role() -> None:
    s = _s()
    s.add(Membership(id="m1", user_id="u1", tenant_id="t1", role="editor"))
    s.commit()
    m = s.get(Membership, "m1")
    assert m.role == "editor" and m.tenant_id == "t1"
