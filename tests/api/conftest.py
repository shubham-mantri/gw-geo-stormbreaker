"""Shared FastAPI API test fixtures (M2-T04), reused by the T13-T16 router tests.

Hermetic: one in-memory SQLite DB (a single connection shared across the TestClient's request
thread via ``StaticPool``), test ``Settings`` with a known JWT secret, and dependency overrides that
point every DB session at that SQLite engine. Tokens are *minted* directly (``auth`` signs them;
``get_current_principal`` only decodes, with no DB round-trip), so a token needs no backing
``AppUser`` row -- ``make_token`` therefore also mints cross-tenant / arbitrary-role tokens for the
tenancy + RBAC tests the downstream router tasks add.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session as SASession
from sqlalchemy.pool import StaticPool

from gw_geo.api import auth, deps
from gw_geo.api.app import create_app
from gw_geo.api.routers import leadcapture
from gw_geo.common.config import Settings
from gw_geo.common.db import AppUser, Base, Membership, Tenant

# >= 32 bytes so PyJWT raises no InsecureKeyLengthWarning across the reused fixtures.
TEST_JWT_SECRET = "test-jwt-secret-0123456789abcdef"


@pytest.fixture
def settings() -> Settings:
    """Test settings: a known JWT secret + in-memory SQLite (never touches Postgres)."""
    return Settings(
        jwt_secret=TEST_JWT_SECRET,
        database_url="sqlite://",
        cors_allow_origins=["http://testserver"],
    )


@pytest.fixture
def engine() -> Engine:
    """One shared in-memory SQLite engine with every table created."""
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def app_client(settings: Settings, engine: Engine) -> Iterator[TestClient]:
    """A ``TestClient`` over the real ``create_app``, with every DB session bound to ``engine``."""
    app = create_app(settings)

    def _test_db() -> Iterator[SASession]:
        session = SASession(engine)
        try:
            yield session
        finally:
            session.close()

    # Authed routes resolve DB via deps.get_db_session; the public leadcapture router has its own
    # get_db_session (create_app points it at deps.get_db_session). Override both so the whole app --
    # authed and public -- talks to the test SQLite DB regardless of override chaining.
    app.dependency_overrides[deps.get_db_session] = _test_db
    app.dependency_overrides[leadcapture.get_db_session] = _test_db
    with TestClient(app) as client:
        yield client


@pytest.fixture
def seeded_user(engine: Engine) -> dict[str, str]:
    """Seed tenant ``t1`` + ``u@x.com`` / ``pw`` (argon2) with an owner ``Membership``.

    Returns the credentials so a test can log in or assert on them.
    """
    with SASession(engine) as session:
        session.add(Tenant(id="t1", name="Acme", sampling_budget_daily=100.0))
        session.add(AppUser(id="u1", email="u@x.com", password_hash=auth.hash_password("pw")))
        session.add(Membership(id="m1", user_id="u1", tenant_id="t1", role="owner"))
        session.commit()
    return {
        "user_id": "u1",
        "email": "u@x.com",
        "password": "pw",
        "tenant_id": "t1",
        "role": "owner",
    }


@pytest.fixture
def make_token(settings: Settings) -> Callable[..., str]:
    """Factory minting a signed *access* token for any user/tenant/role (RBAC + cross-tenant tests)."""

    def _make(*, user_id: str = "u1", tenant_id: str = "t1", role: str = "viewer") -> str:
        return auth.issue_tokens(
            user_id=user_id, tenant_id=tenant_id, role=role, secret=settings.jwt_secret
        ).access_token

    return _make


@pytest.fixture
def viewer_token(make_token: Callable[..., str]) -> str:
    """A valid access token whose role is ``viewer`` (rejected by ``require_role('editor')``)."""
    return make_token(role="viewer")
