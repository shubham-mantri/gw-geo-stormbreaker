"""Opportunity-generation worker tests (W3): run ``build_opportunities`` over a brand's live
visibility data and persist the ranked ``Opportunity`` rows, plus the local refresh-job wrapper.

Hermetic (TRD §12): in-memory / file SQLite with fake snapshots + citations -- no live engine, no
Postgres, no network. (Real-Postgres FK-safety is exercised out-of-band via the ``opportunities``
CLI subcommand, not here: SQLite defaults FK enforcement off, so a FK-ordering bug would slip
through the hermetic suite -- exactly as the recent measurement-runner fix documents.)
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session as SASession
from sqlalchemy.pool import StaticPool

from gw_geo.common.config import Settings
from gw_geo.common.db import Base, Brand, Citation, Opportunity, Prompt, Tenant, VisibilitySnapshot
from gw_geo.orchestration import opportunity_gen
from gw_geo.orchestration.opportunity_gen import (
    generate_and_persist_opportunities,
    run_opportunity_refresh_job,
)
from gw_geo.orchestration.opportunity_service import DbOpportunityService

TENANT = "demo-tenant"
BRAND = "demo-brand"


@pytest.fixture
def engine() -> Engine:
    """One shared in-memory SQLite engine with every table created."""
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    return eng


def _seed_brand(session: SASession) -> None:
    session.add(Tenant(id=TENANT, name=TENANT, sampling_budget_daily=100.0))
    session.add(Brand(id=BRAND, tenant_id=TENANT, name="Gushwork", domain="gushwork.ai"))
    # FK parent for the Citations the source-mix tests seed (citation.prompt_id -> prompt.id).
    session.add(Prompt(id="p1", tenant_id=TENANT, brand_id=BRAND, text="q"))


def _snap(
    engine_name: str, mention: float, *, sentiment: float = 0.0, n: int = 3, sid: str = "s1"
) -> VisibilitySnapshot:
    return VisibilitySnapshot(
        id=sid,
        tenant_id=TENANT,
        brand_id=BRAND,
        engine=engine_name,
        geo="us",
        persona=None,
        date="2026-07-06",
        mention_rate=mention,
        citation_rate=1.0,
        avg_position=None,
        sentiment_score=sentiment,
        share_of_voice=mention,
        n_samples=n,
        ci_low=max(0.0, mention - 0.1),
        ci_high=min(1.0, mention + 0.1),
    )


def test_absence_snapshot_generates_and_persists_opportunity(engine: Engine) -> None:
    # The live Gushwork situation: absent on claude (mention_rate=0.0), n=3.
    with SASession(engine) as session:
        _seed_brand(session)
        session.add(_snap("claude", 0.0))
        session.commit()

    with SASession(engine) as session:
        count = generate_and_persist_opportunities(
            session=session, tenant_id=TENANT, brand_id=BRAND
        )
    assert count == 1

    with SASession(engine) as session:
        rows = session.query(Opportunity).all()
    assert len(rows) == 1
    opp = rows[0]
    assert opp.tenant_id == TENANT and opp.brand_id == BRAND
    assert opp.source_gap == "absence" and opp.engine == "claude"
    assert opp.status == "open"  # what DbOpportunityService surfaces
    # est_impact = weight(n=3 -> 3/10 = 0.3) * gap(1.0 - 0.0 = 1.0) = 0.3
    assert opp.est_impact == pytest.approx(0.3)
    # the absent engine must NOT also emit a spurious "sentiment neutral" opportunity
    assert not any(o.source_gap == "sentiment" for o in rows)


def test_persisted_rows_surface_via_list_for_brand(engine: Engine) -> None:
    # Prove the worker writes rows in exactly the scope/shape DbOpportunityService.list_for_brand
    # reads (tenant/brand scope, est_impact-desc ordering) -- so they actually surface in the API.
    with SASession(engine) as session:
        _seed_brand(session)
        session.add(_snap("claude", 0.0, sid="s1"))  # weight 0.3 * gap 1.0 = 0.30
        session.add(_snap("perplexity", 0.05, n=10, sid="s2"))  # weight 1.0 * gap 0.95 = 0.95
        session.commit()

    with SASession(engine) as session:
        count = generate_and_persist_opportunities(
            session=session, tenant_id=TENANT, brand_id=BRAND
        )
    assert count == 2

    with SASession(engine) as session:
        svc = DbOpportunityService(
            session=session,
            tenant_id=TENANT,
            content_service=None,  # list_for_brand never touches the content pipeline
        )
        rows = svc.list_for_brand(tenant_id=TENANT, brand_id=BRAND)
    assert len(rows) == 2
    assert set(rows[0]) == {"id", "title", "rationale", "est_impact", "engine"}  # ui-spec §6
    # ranked by est_impact desc: perplexity (0.95) outranks claude (0.30)
    assert rows[0]["engine"] == "perplexity"
    assert rows[0]["est_impact"] >= rows[1]["est_impact"]


def test_refresh_is_idempotent(engine: Engine) -> None:
    with SASession(engine) as session:
        _seed_brand(session)
        session.add(_snap("claude", 0.0))
        session.commit()

    with SASession(engine) as session:
        first = generate_and_persist_opportunities(
            session=session, tenant_id=TENANT, brand_id=BRAND
        )
    with SASession(engine) as session:
        second = generate_and_persist_opportunities(
            session=session, tenant_id=TENANT, brand_id=BRAND
        )
    assert first == second == 1

    with SASession(engine) as session:
        rows = session.query(Opportunity).all()
    assert len(rows) == 1  # refreshed in place, never duplicated


def test_refresh_preserves_acted_history(engine: Engine) -> None:
    # A refresh replaces only the OPEN queue; an already-acted opportunity is preserved (act()
    # relies on the status, and the audit trail must survive re-generation).
    with SASession(engine) as session:
        _seed_brand(session)
        session.add(_snap("claude", 0.0))
        session.add(
            Opportunity(
                id="acted-1",
                tenant_id=TENANT,
                brand_id=BRAND,
                title="old (already acted)",
                rationale="old",
                engine="gemini",
                est_impact=0.5,
                source_gap="absence",
                status="acted",
            )
        )
        session.commit()

    with SASession(engine) as session:
        generate_and_persist_opportunities(session=session, tenant_id=TENANT, brand_id=BRAND)

    with SASession(engine) as session:
        statuses = sorted(o.status for o in session.query(Opportunity).all())
    assert "acted" in statuses  # audit trail preserved
    assert statuses.count("open") == 1  # fresh open queue generated


def test_source_mix_computed_from_citations_worker_runs_clean(engine: Engine) -> None:
    # A healthy engine (no absence/sentiment gap) with citations present: the worker computes the
    # source mix from Citation rows and runs cleanly, producing no opportunity (reports=[] so the
    # source mix is not itself scored into an opportunity in this wave).
    with SASession(engine) as session:
        _seed_brand(session)
        session.add(_snap("perplexity", 0.6, sentiment=0.9, n=10))
        session.add(
            Citation(
                id="c1",
                tenant_id=TENANT,
                brand_id=BRAND,
                url="https://x.com/a",
                domain="x.com",
                source_type="other",
                engine="perplexity",
                prompt_id="p1",
                seen_count=3,
            )
        )
        session.commit()

    with SASession(engine) as session:
        count = generate_and_persist_opportunities(
            session=session, tenant_id=TENANT, brand_id=BRAND
        )
    assert count == 0


def test_source_mix_fractions_sum_by_seen_count(engine: Engine) -> None:
    # Unit-check the source-mix helper: fraction of citation volume by source_type, seen_count-
    # weighted (28 'other' + 1 'forum_qa' == the live Gushwork mix).
    with SASession(engine) as session:
        _seed_brand(session)
        for i in range(28):
            session.add(
                Citation(
                    id=f"o{i}",
                    tenant_id=TENANT,
                    brand_id=BRAND,
                    url=f"https://x.com/{i}",
                    domain="x.com",
                    source_type="other",
                    engine="claude",
                    prompt_id="p1",
                    seen_count=1,
                )
            )
        session.add(
            Citation(
                id="f0",
                tenant_id=TENANT,
                brand_id=BRAND,
                url="https://q.com/1",
                domain="q.com",
                source_type="forum_qa",
                engine="claude",
                prompt_id="p1",
                seen_count=1,
            )
        )
        session.commit()

    with SASession(engine) as session:
        mix = opportunity_gen._brand_source_mix(session, tenant_id=TENANT, brand_id=BRAND)
    assert mix["other"] == pytest.approx(28 / 29)
    assert mix["forum_qa"] == pytest.approx(1 / 29)
    assert sum(mix.values()) == pytest.approx(1.0)


def test_missing_brand_returns_zero(engine: Engine) -> None:
    with SASession(engine) as session:
        count = generate_and_persist_opportunities(
            session=session, tenant_id=TENANT, brand_id="does-not-exist"
        )
    assert count == 0


def test_cross_tenant_brand_returns_zero(engine: Engine) -> None:
    # The brand exists but is owned by another tenant: never generate opportunities for it.
    with SASession(engine) as session:
        _seed_brand(session)
        session.add(_snap("claude", 0.0))
        session.commit()

    with SASession(engine) as session:
        count = generate_and_persist_opportunities(
            session=session, tenant_id="other-tenant", brand_id=BRAND
        )
    assert count == 0
    with SASession(engine) as session:
        assert session.query(Opportunity).count() == 0


def test_run_job_opens_session_and_persists(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    # run_opportunity_refresh_job opens its OWN Session from settings.database_url (the unit both
    # the API BackgroundTask and the CLI call). A file SQLite lets the job's fresh engine see the
    # seeded data (mirrors tests/test_cli_schedule.py's all-brands test).
    db_path = tmp_path / "opp.db"
    url = f"sqlite:///{db_path}"
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    with SASession(eng) as session:
        _seed_brand(session)
        session.add(_snap("claude", 0.0))
        session.commit()

    monkeypatch.setattr(opportunity_gen, "get_settings", lambda: Settings(database_url=url))
    count = run_opportunity_refresh_job(tenant_id=TENANT, brand_id=BRAND)
    assert count == 1
    with SASession(eng) as session:
        assert session.query(Opportunity).count() == 1
