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
from gw_geo.common.db import (
    AppUser,
    AttributionLink,
    Base,
    Brand,
    Citation,
    DriftEvent,
    HoldoutCohort,
    Lead,
    Membership,
    Prompt,
    Session,
    Tenant,
    VisibilitySnapshot,
)

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
def editor_token(make_token: Callable[..., str]) -> str:
    """A valid access token scoped to tenant ``t1`` whose role is ``editor`` (rejected by
    ``require_role('admin')`` -- T16's ``POST /integrations/{kind}`` RBAC test)."""
    return make_token(tenant_id="t1", role="editor")


@pytest.fixture
def admin_token(make_token: Callable[..., str]) -> str:
    """A valid access token scoped to tenant ``t1`` whose role is ``admin`` (the minimum T16's
    ``POST /integrations/{kind}`` accepts)."""
    return make_token(tenant_id="t1", role="admin")


@pytest.fixture
def t1_token(make_token: Callable[..., str]) -> str:
    """A valid access token scoped to tenant ``t1`` (the tenant ``seeded_snapshots``/
    ``seeded_citations`` seed brand ``b1`` under). Role ``editor``: the T14 read endpoints apply no
    RBAC gate (``viewer`` would suffice for those), but T16's ``POST /brands/{id}/prompts`` requires
    ``role >= editor`` and reuses this same fixture (per its task spec's failing test) -- bumped
    here so both keep working from one fixture rather than forking a near-duplicate.
    """
    return make_token(tenant_id="t1", role="editor")


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


@pytest.fixture
def seeded_full_attribution(engine: Engine) -> None:
    """Seed tenant ``t1``'s brand ``b1`` with sessions/leads/attribution-links spanning all three
    lead-level methods (``direct``/``citation_linked``/``assisted``) plus a holdout cohort whose
    tagged side converts worse than its optimized remainder -- so ``GET /brands/{id}/pipeline``
    (T15, backed by ``attribution.pipeline.pipeline_view``, T10) reports genuinely non-zero figures
    under all four ``method_breakdown`` keys, not just an always-present-but-empty key set.

    Timestamps are "now" (like ``seeded_citations``) so they land inside the endpoint's
    ``range=90d`` window regardless of the day a test runs. This is a *separate* fixture of the
    same name as ``tests/attribution/test_pipeline.py``'s (file-local there, over its own ad hoc
    engine) -- this one seeds the API tests' shared ``engine`` fixture instead, which is what the
    ``app_client``/``get_db_session`` override actually reads from.
    """
    now = datetime.now(timezone.utc)
    with SASession(engine) as session:
        session.add(Tenant(id="t1", name="Acme", sampling_budget_daily=100.0))
        session.add(
            Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com", competitors=["Beta"])
        )
        session.add(
            Prompt(id="p-cta", tenant_id="t1", brand_id="b1", text="best CRM for SaaS startups")
        )

        for sid, engine_name in (("s1", "perplexity"), ("s2", "perplexity"), ("s3", "chatgpt")):
            session.add(
                Session(
                    id=sid,
                    tenant_id="t1",
                    brand_id="b1",
                    visitor_id=f"v-{sid}",
                    landing_url=f"https://acme.com/{sid}",
                    referrer=None,
                    utm={},
                    engine=engine_name,
                    ts=now,
                )
            )
        session.add(
            Lead(id="l1", tenant_id="t1", brand_id="b1", visitor_id="v-s1", session_id="s1",
                 value_usd=100.0, ts=now)
        )
        session.add(
            Lead(id="l2", tenant_id="t1", brand_id="b1", visitor_id="v-s2", session_id="s2",
                 value_usd=200.0, ts=now)
        )
        session.add(
            Lead(id="l3", tenant_id="t1", brand_id="b1", visitor_id="v-s3", session_id="s3",
                 value_usd=50.0, ts=now)
        )

        # direct (l1, strongest -- via session s1)
        session.add(
            AttributionLink(id="lk-d1", tenant_id="t1", brand_id="b1", lead_id="l1",
                             session_id="s1", citation_id=None, prompt_id=None,
                             engine="perplexity", method="direct", confidence="high",
                             value_usd=100.0, ts=now)
        )
        # citation_linked (l2, via session s2 -- reaches the lead through its session)
        session.add(
            AttributionLink(id="lk-c1", tenant_id="t1", brand_id="b1", lead_id=None,
                             session_id="s2", citation_id=None, prompt_id="p-cta",
                             engine="perplexity", method="citation_linked", confidence="high",
                             value_usd=None, ts=now)
        )
        # assisted (l3, via session s3)
        session.add(
            AttributionLink(id="lk-a1", tenant_id="t1", brand_id="b1", lead_id="l3",
                             session_id="s3", citation_id=None, prompt_id=None,
                             engine="chatgpt", method="assisted", confidence="reported",
                             value_usd=50.0, ts=now)
        )

        # holdout cohort: the tagged (holdout) side converts worse than the optimized remainder,
        # so measure_incrementality yields a positive lift and thus holdout_incremental > 0.
        session.add(
            HoldoutCohort(id="ho1", tenant_id="t1", brand_id="b1", name="Q_holdout",
                          kind="prompt", prompt_ids=["p-hold"], is_holdout=True, started_at=now)
        )
        for i in range(4):
            sid = f"hold-s{i}"
            session.add(
                Session(id=sid, tenant_id="t1", brand_id="b1", visitor_id=f"v-{sid}",
                        landing_url="https://acme.com/hold", referrer=None,
                        utm={"prompt_id": "p-hold"}, engine=None, ts=now)
            )
            if i == 0:  # 1/4 converts on the un-optimized holdout side
                session.add(
                    Lead(id=f"hold-l{i}", tenant_id="t1", brand_id="b1", visitor_id=f"v-{sid}",
                         session_id=sid, value_usd=500.0, ts=now)
                )
        for i in range(4):
            sid = f"opt-s{i}"
            session.add(
                Session(id=sid, tenant_id="t1", brand_id="b1", visitor_id=f"v-{sid}",
                        landing_url="https://acme.com/opt", referrer=None,
                        utm={"prompt_id": "p-opt"}, engine=None, ts=now)
            )
            if i < 3:  # 3/4 convert on the optimized side
                session.add(
                    Lead(id=f"opt-l{i}", tenant_id="t1", brand_id="b1", visitor_id=f"v-{sid}",
                         session_id=sid, value_usd=500.0, ts=now)
                )
        session.commit()


@pytest.fixture
def seeded_drift(engine: Engine) -> None:
    """Seed tenant ``t1``'s brand ``b1`` (ownership, for ``/alerts``'s brand-scope check) plus one
    breached, system-level ``DriftEvent`` for ``GET /brands/{id}/alerts`` (T15).

    ``DriftEvent`` carries no ``tenant_id``/``brand_id`` (m1-design §6: engine drift is a property
    of the engine/canary, not of any one tenant), so this alert is visible to any tenant that owns
    *a* brand -- it is not scoped to ``b1`` specifically.
    """
    with SASession(engine) as session:
        session.add(Tenant(id="t1", name="Acme", sampling_budget_daily=100.0))
        session.add(
            Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com", competitors=["Beta"])
        )
        session.add(
            DriftEvent(
                id="d1",
                engine="chatgpt",
                canary_id="chatgpt-crm-baseline",
                baseline_rate=0.9,
                observed_rate=0.5,
                drop=0.4,
                breached=True,
                retrain_flag=True,
                ts=datetime.now(timezone.utc),
            )
        )
        session.commit()
