import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SASession

from gw_geo.api import auth
from gw_geo.common.config import Settings
from gw_geo.common.db import AppUser, Base, Membership


def test_hash_roundtrip():
    h = auth.hash_password("hunter2")
    assert auth.verify_password("hunter2", h) and not auth.verify_password("x", h)


def test_token_roundtrip_carries_tenant_and_role():
    tp = auth.issue_tokens(user_id="u1", tenant_id="t1", role="editor", secret="k")
    p = auth.decode_token(tp.access_token, secret="k")
    assert p.user_id == "u1" and p.tenant_id == "t1" and p.role == "editor"


def test_expired_token_raises():
    tp = auth.issue_tokens(user_id="u1", tenant_id="t1", role="viewer", secret="k", access_ttl_s=-1)
    with pytest.raises(auth.AuthError):
        auth.decode_token(tp.access_token, secret="k")


def test_rbac_ordering():
    assert auth.role_at_least("admin", "editor")
    assert not auth.role_at_least("viewer", "editor")


def _seeded_session() -> SASession:
    """A hermetic in-memory SQLite session seeded with one `AppUser` + `Membership`."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = SASession(engine)
    session.add(
        AppUser(id="u1", email="a@x.com", password_hash=auth.hash_password("hunter2"))
    )
    session.add(Membership(id="m1", user_id="u1", tenant_id="t1", role="editor"))
    session.commit()
    return session


def test_authenticate_resolves_seeded_user_and_membership():
    session = _seeded_session()
    settings = Settings(jwt_secret="k")

    tp = auth.authenticate(
        session, email="a@x.com", password="hunter2", secret="k", settings=settings
    )

    assert tp.tenant_id == "t1" and tp.role == "editor"
    principal = auth.decode_token(tp.access_token, secret="k")
    assert principal.user_id == "u1" and principal.tenant_id == "t1" and principal.role == "editor"


def test_authenticate_rejects_wrong_password():
    session = _seeded_session()
    settings = Settings(jwt_secret="k")

    with pytest.raises(auth.AuthError):
        auth.authenticate(
            session, email="a@x.com", password="wrong", secret="k", settings=settings
        )


def test_authenticate_rejects_unknown_email():
    session = _seeded_session()
    settings = Settings(jwt_secret="k")

    with pytest.raises(auth.AuthError):
        auth.authenticate(
            session, email="nobody@x.com", password="hunter2", secret="k", settings=settings
        )
