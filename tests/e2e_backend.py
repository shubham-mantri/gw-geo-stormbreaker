"""Seeded FastAPI entrypoint for the Playwright E2E (M2-T21) -- the real ``create_app`` over a
file-backed, seeded SQLite DB, with **no live services**.

Playwright's ``webServer`` boots this via ``uvicorn tests.e2e_backend:app``; the ``web/`` dashboard
reaches it through a same-origin Next.js rewrite (see ``web/next.config.mjs`` +
``web/playwright.config.ts``), so the browser is never cross-origin and CORS never blocks the E2E.

Two tenants are seeded (``t1``/``t2``) with owners ``owner@t1.com`` / ``owner@t2.com`` (password
``pw``, argon2-hashed via :func:`gw_geo.api.auth.hash_password`), brands ``b1`` (t1) / ``b2`` (t2),
and enough visibility + attribution data that ``/brands/b1/overview`` and ``/brands/b1/pipeline``
render non-empty for ``t1``. ``b1`` belongs to ``t1`` only -- the cross-tenant isolation gate
(``owner@t2.com`` -> ``/brands/b1/*`` -> 404) hangs off that.

DB sessions are wired via ``dependency_overrides`` (exactly like ``tests/api/conftest.py``) to a
single ``StaticPool`` connection with ``check_same_thread=False`` -- safe under FastAPI's sync-route
threadpool without touching the binding ``create_app`` interface. The file is rebuilt from scratch
on every process start, so each ``playwright test`` run starts from a known seed. This module touches
the filesystem at import (by design), which is why it is an e2e entrypoint under ``tests/`` and is
never imported by the unit suite.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session as SASession

from gw_geo.api import auth, deps
from gw_geo.api.app import create_app
from gw_geo.api.routers import leadcapture
from gw_geo.common.config import Settings
from gw_geo.common.db import (
    AnswerExtraction,
    AppUser,
    AttributionLink,
    Base,
    Brand,
    Citation,
    DriftEvent,
    HoldoutCohort,
    Lead,
    Membership,
    ProbeRun,
    Prompt,
    Tenant,
    VisibilitySnapshot,
)
from gw_geo.common.db import Session as PixelSession

_DB_PATH = os.environ.get("GEO_E2E_DB") or os.path.join(
    tempfile.gettempdir(), "gw_geo_e2e.sqlite"
)
_DB_URL = f"sqlite:///{_DB_PATH}"
# >= 32 bytes so PyJWT emits no InsecureKeyLengthWarning; consistent across a single app instance.
_JWT_SECRET = os.environ.get("GEO_E2E_JWT_SECRET", "e2e-jwt-secret-0123456789abcdef-pad")
_PIXEL_SALT = "e2e-pixel-salt"


def _seed(engine: Engine) -> None:
    """Populate ``engine`` with two tenants + owners + brands and non-empty t1/b1 dashboard data."""
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    now = datetime.now(timezone.utc)

    with SASession(engine) as s:
        # --- tenants, users (owner@t1.com / owner@t2.com, password "pw"), memberships ---
        s.add(Tenant(id="t1", name="Acme", sampling_budget_daily=100.0))
        s.add(Tenant(id="t2", name="Globex", sampling_budget_daily=100.0))
        s.add(AppUser(id="u-t1", email="owner@t1.com", password_hash=auth.hash_password("pw")))
        s.add(AppUser(id="u-t2", email="owner@t2.com", password_hash=auth.hash_password("pw")))
        s.add(Membership(id="m-t1", user_id="u-t1", tenant_id="t1", role="owner"))
        s.add(Membership(id="m-t2", user_id="u-t2", tenant_id="t2", role="owner"))

        # --- brands: b1 belongs to t1 only (the isolation gate), b2 to t2 ---
        s.add(Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com", competitors=["Beta"]))
        s.add(Brand(id="b2", tenant_id="t2", name="Globex", domain="globex.com", competitors=[]))

        # --- t1/b1 visibility snapshots (overview KPIs + trend, visibility per-engine row) ---
        for i, (date, mention) in enumerate([(yesterday.isoformat(), 0.3), (today.isoformat(), 0.5)]):
            s.add(
                VisibilitySnapshot(
                    id=f"vs-{i}", tenant_id="t1", brand_id="b1", engine="perplexity", geo="us",
                    persona=None, date=date, mention_rate=mention, citation_rate=0.4,
                    avg_position=2.0, sentiment_score=0.5, share_of_voice=0.3, n_samples=20,
                    ci_low=0.2, ci_high=0.6,
                )
            )
        # A second tenant's snapshot in the same window -- must never leak into t1's response.
        s.add(
            VisibilitySnapshot(
                id="vs-t2", tenant_id="t2", brand_id="b2", engine="perplexity", geo="us",
                persona=None, date=today.isoformat(), mention_rate=0.9, citation_rate=0.9,
                avg_position=1.0, sentiment_score=1.0, share_of_voice=0.9, n_samples=10,
                ci_low=0.8, ci_high=1.0,
            )
        )

        # --- t1/b1 prompt + one probed answer (visibility prompt table + settings prompt list) ---
        s.add(Prompt(id="p-cta", tenant_id="t1", brand_id="b1", text="best CRM for SaaS startups",
                     intent_cluster="comparison", geo="us"))
        s.add(ProbeRun(id="pr-1", tenant_id="t1", prompt_id="p-cta", engine="perplexity", geo="us",
                       persona=None, ts=now, status="ok", cost_usd=0.01, latency_ms=800))
        s.add(AnswerExtraction(id="ae-1", tenant_id="t1", probe_run_id="pr-1", brand_mentioned=True,
                               position=2, sentiment="positive", cited_urls=["https://acme.com"],
                               competitors_present=["Beta"]))

        # --- t1/b1 citations (sources map) ---
        s.add(Citation(id="ci-reddit", tenant_id="t1", brand_id="b1", url="https://reddit.com/r/x/1",
                       domain="reddit.com", source_type="reddit", engine="perplexity",
                       prompt_id="p-cta", first_seen=now, last_seen=now, seen_count=3))
        s.add(Citation(id="ci-own", tenant_id="t1", brand_id="b1", url="https://acme.com/about",
                       domain="acme.com", source_type="own_site", engine="perplexity",
                       prompt_id="p-cta", first_seen=now, last_seen=now, seen_count=1))

        # --- t1/b1 attribution: sessions + leads + links across all three lead-level methods ---
        for sid, eng in (("s1", "perplexity"), ("s2", "perplexity"), ("s3", "chatgpt")):
            s.add(PixelSession(id=sid, tenant_id="t1", brand_id="b1", visitor_id=f"v-{sid}",
                               landing_url=f"https://acme.com/{sid}", utm={}, engine=eng, ts=now))
        s.add(Lead(id="l1", tenant_id="t1", brand_id="b1", visitor_id="v-s1", session_id="s1",
                   value_usd=210_000.0, ts=now))
        s.add(Lead(id="l2", tenant_id="t1", brand_id="b1", visitor_id="v-s2", session_id="s2",
                   value_usd=88_000.0, ts=now))
        s.add(Lead(id="l3", tenant_id="t1", brand_id="b1", visitor_id="v-s3", session_id="s3",
                   value_usd=50_000.0, ts=now))
        s.add(AttributionLink(id="lk-d", tenant_id="t1", brand_id="b1", lead_id="l1", session_id="s1",
                              engine="perplexity", method="direct", confidence="high",
                              value_usd=210_000.0, ts=now))
        s.add(AttributionLink(id="lk-c", tenant_id="t1", brand_id="b1", session_id="s2",
                              prompt_id="p-cta", engine="perplexity", method="citation_linked",
                              confidence="high", ts=now))
        s.add(AttributionLink(id="lk-a", tenant_id="t1", brand_id="b1", lead_id="l3", session_id="s3",
                              engine="chatgpt", method="assisted", confidence="reported",
                              value_usd=50_000.0, ts=now))

        # --- holdout experiment: a holdout cohort (p-hold) vs a symmetric optimized cohort (p-opt);
        # the tagged holdout side converts worse than the tagged optimized cohort -> positive lift.
        # Both arms are cohort-scoped (m2-design §2.5), so the optimized cohort is seeded explicitly
        # (the untagged direct/assisted sessions s1-s3 are in neither arm) -- otherwise the optimized
        # arm is empty and the Pipeline screen's incremental lift degrades to 0. ---
        s.add(HoldoutCohort(id="ho-1", tenant_id="t1", brand_id="b1", name="Q_holdout", kind="prompt",
                            prompt_ids=["p-hold"], is_holdout=True, started_at=now))
        s.add(HoldoutCohort(id="ho-1-opt", tenant_id="t1", brand_id="b1", name="Q_optimized",
                            kind="prompt", prompt_ids=["p-opt"], is_holdout=False, started_at=now))
        for i in range(4):
            hsid = f"hold-s{i}"
            s.add(PixelSession(id=hsid, tenant_id="t1", brand_id="b1", visitor_id=f"v-{hsid}",
                               landing_url="https://acme.com/hold", utm={"prompt_id": "p-hold"}, ts=now))
            if i == 0:  # 1/4 convert on the un-optimized holdout cohort
                s.add(Lead(id=f"hold-l{i}", tenant_id="t1", brand_id="b1", visitor_id=f"v-{hsid}",
                           session_id=hsid, value_usd=500.0, ts=now))
        for i in range(4):
            osid = f"opt-s{i}"
            s.add(PixelSession(id=osid, tenant_id="t1", brand_id="b1", visitor_id=f"v-{osid}",
                               landing_url="https://acme.com/opt", utm={"prompt_id": "p-opt"}, ts=now))
            if i < 3:  # 3/4 convert on the optimized cohort
                s.add(Lead(id=f"opt-l{i}", tenant_id="t1", brand_id="b1", visitor_id=f"v-{osid}",
                           session_id=osid, value_usd=500.0, ts=now))

        # --- one breached, system-level drift event (alerts feed) ---
        s.add(DriftEvent(id="d1", engine="chatgpt", canary_id="chatgpt-crm-baseline",
                         baseline_rate=0.9, observed_rate=0.5, drop=0.4, breached=True,
                         retrain_flag=True, ts=now))
        s.commit()


def _build_app() -> object:
    """Rebuild the seeded SQLite DB from scratch and return the wired, seeded FastAPI app."""
    parent = os.path.dirname(_DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.exists(_DB_PATH):
        os.remove(_DB_PATH)
    # File-backed SQLite with the default pool: each request gets its OWN connection (unlike the
    # unit tests' in-memory StaticPool, which shares one connection -- unsafe here, since FastAPI
    # runs sync routes on a threadpool and concurrent use of one SQLite connection raises
    # "API misuse"). ``check_same_thread=False`` lets the pool hand a connection to any worker
    # thread; all connections see the same file, so the seed is visible to every request.
    engine = create_engine(_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    _seed(engine)

    app = create_app(
        Settings(
            database_url=_DB_URL,
            jwt_secret=_JWT_SECRET,
            pixel_write_key_salt=_PIXEL_SALT,
            cors_allow_origins=["http://localhost:4488", "http://127.0.0.1:4488"],
        )
    )

    def _db() -> Iterator[SASession]:
        session = SASession(engine)
        try:
            yield session
        finally:
            session.close()

    # Bind every DB session (authed + public leadcapture) to the seeded engine -- see module docstring.
    app.dependency_overrides[deps.get_db_session] = _db
    app.dependency_overrides[leadcapture.get_db_session] = _db
    return app


app = _build_app()
