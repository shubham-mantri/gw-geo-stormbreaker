"""Tests for the local attribution-reconcile trigger (W4): run the fuzzy attribution writers over a
brand's captured sessions+leads and persist the ``attribution_link`` rows the pipeline reads, plus
the local reconcile-job wrapper.

Hermetic (TRD §12): in-memory / file SQLite with directly-seeded sessions/leads -- no live engine,
no Postgres, no network. (Real-Postgres FK-safety is exercised out-of-band via the runnable
scratch script + the ``reconcile`` CLI subcommand, not here: SQLite defaults FK enforcement off, so
a FK-ordering bug would slip through the hermetic suite -- exactly as the measurement-runner /
opportunity-gen fixes document. This batch only ever inserts leaf ``attribution_link`` rows whose
``session``/``lead`` parents were committed by an earlier ``/lead-capture/collect``, so it is
FK-safe by construction.)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session as SASession
from sqlalchemy.pool import StaticPool

from gw_geo.attribution import trigger
from gw_geo.attribution.pipeline import pipeline_view
from gw_geo.attribution.trigger import reconcile_attribution, run_attribution_reconcile_job
from gw_geo.common.config import Settings
from gw_geo.common.db import AttributionLink, Base, Brand, Lead, Tenant
from gw_geo.common.db import Session as SessionRow

TENANT = "t1"
BRAND = "b1"
SINCE = "2026-06-01"
UNTIL = "2026-07-02"
_TS = datetime(2026, 6, 15, tzinfo=timezone.utc)


@pytest.fixture
def engine() -> Engine:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    return eng


def _seed_referred_lead(session: SASession, *, referrer: str, value_usd: float) -> None:
    """Seed tenant+brand, then an AI-referred session, then a lead on that session -- each parent
    committed before its FK child, exactly the order a real Postgres FK graph requires."""
    session.add(Tenant(id=TENANT, name="Acme", sampling_budget_daily=100.0))
    session.add(Brand(id=BRAND, tenant_id=TENANT, name="Acme", domain="acme.com", competitors=[]))
    session.commit()
    session.add(
        SessionRow(
            id="s1",
            tenant_id=TENANT,
            brand_id=BRAND,
            visitor_id="v1",
            landing_url="https://acme.com/crm",
            referrer=referrer,
            utm={},
            engine=None,
            ts=_TS,
        )
    )
    session.commit()
    session.add(
        Lead(
            id="l1",
            tenant_id=TENANT,
            brand_id=BRAND,
            visitor_id="v1",
            session_id="s1",
            email="buyer@x.com",
            value_usd=value_usd,
            ts=_TS,
        )
    )
    session.commit()


def test_reconcile_creates_direct_link_and_pipeline_reflects_value(engine: Engine) -> None:
    # The core W4 flow: an AI-referred session (perplexity) that converted to a $500 lead, with NO
    # attribution_link yet -> reconcile creates a direct link -> the pipeline reports the value.
    with SASession(engine) as s:
        _seed_referred_lead(s, referrer="https://www.perplexity.ai/", value_usd=500.0)

    with SASession(engine) as s:
        # Before reconcile: pipeline sees the lead but no link, so nothing is attributed.
        before = pipeline_view(s, tenant_id=TENANT, brand_id=BRAND, since=SINCE, until=UNTIL)
        assert before["influenced"] == 0.0 and before["attributed"] == 0.0

    with SASession(engine) as s:
        counts = reconcile_attribution(
            session=s, tenant_id=TENANT, brand_id=BRAND, since=SINCE, until=UNTIL
        )
    assert counts["direct"] == 1

    with SASession(engine) as s:
        links = s.query(AttributionLink).all()
        assert len(links) == 1
        link = links[0]
        assert link.method == "direct" and link.engine == "perplexity"
        assert link.confidence == "high"
        assert link.session_id == "s1" and link.lead_id == "l1"
        assert link.value_usd == 500.0
        # session.engine is stamped back by mechanism 1.
        assert s.get(SessionRow, "s1").engine == "perplexity"

    with SASession(engine) as s:
        after = pipeline_view(s, tenant_id=TENANT, brand_id=BRAND, since=SINCE, until=UNTIL)
    assert after["influenced"] == 500.0
    assert after["attributed"] == 500.0
    assert after["leads"] == 1
    assert after["method_breakdown"]["direct"] == 500.0


def test_reconcile_is_idempotent(engine: Engine) -> None:
    # Re-running reconcile upserts the one direct link per session in place, never duplicating it.
    with SASession(engine) as s:
        _seed_referred_lead(s, referrer="https://chatgpt.com/c/abc", value_usd=200.0)

    with SASession(engine) as s:
        first = reconcile_attribution(
            session=s, tenant_id=TENANT, brand_id=BRAND, since=SINCE, until=UNTIL
        )
    with SASession(engine) as s:
        second = reconcile_attribution(
            session=s, tenant_id=TENANT, brand_id=BRAND, since=SINCE, until=UNTIL
        )
    assert first["direct"] == second["direct"] == 1
    with SASession(engine) as s:
        assert s.query(AttributionLink).count() == 1


def test_non_ai_referrer_produces_no_link(engine: Engine) -> None:
    with SASession(engine) as s:
        _seed_referred_lead(s, referrer="https://google.com/search?q=acme", value_usd=99.0)

    with SASession(engine) as s:
        counts = reconcile_attribution(
            session=s, tenant_id=TENANT, brand_id=BRAND, since=SINCE, until=UNTIL
        )
    assert counts == {"direct": 0, "citation_linked": 0, "assisted": 0}
    with SASession(engine) as s:
        assert s.query(AttributionLink).count() == 0


def test_missing_brand_returns_zero(engine: Engine) -> None:
    with SASession(engine) as s:
        counts = reconcile_attribution(
            session=s, tenant_id=TENANT, brand_id="does-not-exist", since=SINCE, until=UNTIL
        )
    assert counts == {"direct": 0, "citation_linked": 0, "assisted": 0}


def test_cross_tenant_brand_returns_zero(engine: Engine) -> None:
    with SASession(engine) as s:
        _seed_referred_lead(s, referrer="https://www.perplexity.ai/", value_usd=500.0)

    with SASession(engine) as s:
        counts = reconcile_attribution(
            session=s, tenant_id="other-tenant", brand_id=BRAND, since=SINCE, until=UNTIL
        )
    assert counts == {"direct": 0, "citation_linked": 0, "assisted": 0}
    with SASession(engine) as s:
        assert s.query(AttributionLink).count() == 0


def test_run_job_opens_session_and_persists(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # run_attribution_reconcile_job opens its OWN Session from settings.database_url (the unit both
    # the API BackgroundTask and the CLI call). A file SQLite lets the job's fresh engine see the
    # seeded data (mirrors tests/orchestration/test_opportunity_gen.py's run-job test).
    db_path = tmp_path / "attr.db"
    url = f"sqlite:///{db_path}"
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    with SASession(eng) as s:
        _seed_referred_lead(s, referrer="https://www.perplexity.ai/", value_usd=500.0)

    monkeypatch.setattr(trigger, "get_settings", lambda: Settings(database_url=url))
    # Explicit window so the default trailing look-back does not exclude the fixed 2026-06-15 ts.
    counts = run_attribution_reconcile_job(
        tenant_id=TENANT, brand_id=BRAND, since=SINCE, until=UNTIL
    )
    assert counts["direct"] == 1
    with SASession(eng) as s:
        assert s.query(AttributionLink).count() == 1
