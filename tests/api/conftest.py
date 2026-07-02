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
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session as SASession
from sqlalchemy.pool import StaticPool

from gw_geo.api import auth, deps
from gw_geo.api.app import create_app
from gw_geo.api.routers import leadcapture
from gw_geo.common.config import Settings
from gw_geo.common.db import AppUser, Base, Brand, Citation, Membership, Tenant, VisibilitySnapshot

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


@pytest.fixture
def t1_token(make_token: Callable[..., str]) -> str:
    """A valid access token scoped to tenant ``t1`` (the tenant ``seeded_snapshots``/
    ``seeded_citations`` seed brand ``b1`` under). ``viewer`` is enough: the T14 read endpoints
    apply no RBAC gate, only tenant scoping.
    """
    return make_token(tenant_id="t1", role="viewer")


@pytest.fixture
def t2_token(make_token: Callable[..., str]) -> str:
    """A valid access token for a *different* tenant (``t2``) -- for cross-tenant isolation tests
    against brand ``b1`` (owned by ``t1``)."""
    return make_token(tenant_id="t2", role="viewer")


@pytest.fixture
def seeded_brands(engine: Engine) -> None:
    """Seed tenant ``t1``'s brand ``b1`` and tenant ``t2``'s brand ``b2`` -- ownership only, no
    snapshot/citation/lead data -- for ``GET``/``POST /brands`` tenant-scoping tests (T13). Uses the
    same ids/tenants as ``seeded_snapshots`` so a test can seed just brand ownership without pulling
    in unrelated visibility data.
    """
    with SASession(engine) as session:
        session.add(Tenant(id="t1", name="Acme", sampling_budget_daily=100.0))
        session.add(
            Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com", competitors=["Beta"])
        )
        session.add(Tenant(id="t2", name="Globex", sampling_budget_daily=100.0))
        session.add(
            Brand(id="b2", tenant_id="t2", name="Globex", domain="globex.com", competitors=[])
        )
        session.commit()


@pytest.fixture
def seeded_snapshots(engine: Engine) -> None:
    """Seed tenant ``t1``'s brand ``b1`` with two dates' worth of ``VisibilitySnapshot`` rows for
    engine ``perplexity``, each carrying its own ``ci_low``/``ci_high`` + ``n_samples`` -- the
    confidence data ``GET /brands/{id}/visibility`` must expose per engine row (T14).

    Dates are computed relative to "now" (rather than hardcoded) so the fixture -- paired with the
    router's default ``range=30d`` window, itself resolved from "now" -- stays correct regardless
    of the wall-clock date a test happens to run on. Also seeds a second tenant (``t2``/``b2``)
    with its own snapshot, matching the ``tests/measurement/test_feed.py`` convention of always
    seeding a second tenant so cross-tenant leakage fails loudly rather than silently.
    """
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    with SASession(engine) as session:
        session.add(Tenant(id="t1", name="Acme", sampling_budget_daily=100.0))
        session.add(
            Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com", competitors=["Beta"])
        )
        session.add(Tenant(id="t2", name="Globex", sampling_budget_daily=100.0))
        session.add(
            Brand(id="b2", tenant_id="t2", name="Globex", domain="globex.com", competitors=[])
        )
        for i, (date, mention_rate) in enumerate(
            [(yesterday.isoformat(), 0.3), (today.isoformat(), 0.5)]
        ):
            session.add(
                VisibilitySnapshot(
                    id=f"s-t1-{i}",
                    tenant_id="t1",
                    brand_id="b1",
                    engine="perplexity",
                    geo="us",
                    persona=None,
                    date=date,
                    mention_rate=mention_rate,
                    citation_rate=0.4,
                    avg_position=2.0,
                    sentiment_score=0.5,
                    share_of_voice=0.3,
                    n_samples=20,
                    ci_low=0.2,
                    ci_high=0.6,
                )
            )
        # A second tenant's snapshot in the same window -- must never leak into t1's response.
        session.add(
            VisibilitySnapshot(
                id="s-t2-0",
                tenant_id="t2",
                brand_id="b2",
                engine="perplexity",
                geo="us",
                persona=None,
                date=today.isoformat(),
                mention_rate=0.9,
                citation_rate=0.9,
                avg_position=1.0,
                sentiment_score=1.0,
                share_of_voice=0.9,
                n_samples=10,
                ci_low=0.8,
                ci_high=1.0,
            )
        )
        session.commit()


@pytest.fixture
def seeded_citations(engine: Engine) -> None:
    """Seed tenant ``t1``'s brand ``b1`` with ``Citation`` rows across two domains/source types,
    "seen" now (so the router's default ``range=30d`` window covers them) -- backs
    ``GET /brands/{id}/sources`` (T14).
    """
    now = datetime.now(timezone.utc)
    with SASession(engine) as session:
        session.add(Tenant(id="t1", name="Acme", sampling_budget_daily=100.0))
        session.add(
            Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com", competitors=["Beta"])
        )
        session.add(
            Citation(
                id="c-reddit",
                tenant_id="t1",
                brand_id="b1",
                url="https://reddit.com/r/acme/1",
                domain="reddit.com",
                source_type="reddit",
                engine="perplexity",
                prompt_id="p1",
                first_seen=now,
                last_seen=now,
                seen_count=3,
            )
        )
        session.add(
            Citation(
                id="c-own",
                tenant_id="t1",
                brand_id="b1",
                url="https://acme.com/about",
                domain="acme.com",
                source_type="own_site",
                engine="perplexity",
                prompt_id="p1",
                first_seen=now,
                last_seen=now,
                seen_count=1,
            )
        )
        session.commit()
