"""Tests for the local off-site-seeding discovery trigger (m4 seeding live-wiring).

Hermetic (TRD S12): SQLite with FK enforcement ON. The core `run_seeding_discovery` takes the
`SourceMap` / `BriefLLM` / kb-factory injected, so tests pass fakes and make NO live DB-source /
LLM / network call. `run_seeding_discovery_job` opens its OWN session from `settings.database_url`
(patched to a file SQLite) and wires the real `CitationSourceMap`; with no LLM key configured it
skips briefing, so the job stays hermetic and never touches a gateway.

WHITE-HAT INVARIANT (PRD NG1) under test: discovery+briefing NEVER runs the compliance gate, NEVER
places, and NEVER posts. Every task it creates is left at `todo` or `briefed` with
`compliance_status="pending"`, an empty `compliance_report`, and no `placed_url`/`actor` -- there is
no auto-poster, and `mark_placed` remains a separate, human-only, gated step (next wave).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session as SASession
from sqlalchemy.pool import StaticPool

from gw_geo.common.config import Settings
from gw_geo.common.db import AnswerExtraction, Base, Brand, Prompt, ProbeRun, SeedingTask, Tenant
from gw_geo.common.models import Fact
from gw_geo.seeding import trigger
from gw_geo.seeding.channels import ChannelCatalog, seed_channels
from gw_geo.seeding.trigger import run_seeding_discovery, run_seeding_discovery_job

TENANT = "t1"
BRAND = "b1"
SINCE = "2026-06-01"
UNTIL = "2026-06-30"
_IN_WINDOW = datetime(2026, 6, 15, tzinfo=timezone.utc)


class FakeSourceMap:
    """Two competitor-heavy sources: reddit (gap 0.7) ranks above quora (gap 0.5)."""

    def citation_source_mix(self, *, tenant_id: str, brand_id: str, since: str, until: str) -> dict:
        return {"sources": [
            {"domain": "reddit.com", "source_type": "reddit", "engine": "perplexity",
             "you_pct": 0.1, "competitor_pct": 0.8},
            {"domain": "quora.com", "source_type": "forum_qa", "engine": "chatgpt",
             "you_pct": 0.0, "competitor_pct": 0.5},
        ]}


class FakeBriefLLM:
    """Echoes the provided facts as talking points (so grounding keeps them)."""

    def draft_brief(self, *, target, facts, disclosure) -> dict:
        return {"talking_points": list(facts), "format_notes": f"for {target.channel}",
                "target_url": None}


def _fake_kb_factory(brand_id: str):
    class _FakeKB:
        def ground(self, query: str, *, top_k: int = 5) -> list[Fact]:
            return [Fact(id="f1", brand_id=brand_id, text="Acme integrates with X")]

    return _FakeKB()


@pytest.fixture
def engine() -> Engine:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    return eng


def _seed_brand(session: SASession) -> None:
    session.add(Tenant(id=TENANT, name="Acme", sampling_budget_daily=100.0))
    session.commit()
    session.add(
        Brand(id=BRAND, tenant_id=TENANT, name="Acme", domain="acme.com", competitors=["Beta"])
    )
    session.commit()


def _assert_all_pre_placement(tasks: list[SeedingTask]) -> None:
    """The white-hat invariant: nothing was compliance-run, placed, or posted."""
    for task in tasks:
        assert task.status in ("todo", "briefed")
        assert task.compliance_status == "pending"
        assert task.compliance_report == {}
        assert task.placed_url is None
        assert task.actor is None


def test_creates_briefed_tasks_and_returns_count(engine: Engine) -> None:
    with SASession(engine) as s:
        _seed_brand(s)

    with SASession(engine) as s:
        count = run_seeding_discovery(
            session=s, tenant_id=TENANT, brand_id=BRAND, since=SINCE, until=UNTIL,
            source_map=FakeSourceMap(), channels=ChannelCatalog.default(),
            brief_llm=FakeBriefLLM(), kb_factory=_fake_kb_factory,
        )
    assert count == 2

    with SASession(engine) as s:
        tasks = s.query(SeedingTask).order_by(SeedingTask.channel).all()
        assert [t.channel for t in tasks] == ["quora", "reddit"]
        _assert_all_pre_placement(tasks)
        # Briefed: a grounded brief was attached (talking points survive grounding).
        for t in tasks:
            assert t.status == "briefed"
            assert t.brief_ref is not None
            assert "Acme integrates with X" in t.brief_ref


def test_without_llm_stops_at_todo(engine: Engine) -> None:
    # No brief_llm -> tasks are created but never briefed; still the human-in-the-loop start state.
    with SASession(engine) as s:
        _seed_brand(s)
    with SASession(engine) as s:
        count = run_seeding_discovery(
            session=s, tenant_id=TENANT, brand_id=BRAND, since=SINCE, until=UNTIL,
            source_map=FakeSourceMap(), channels=ChannelCatalog.default(),
        )
    assert count == 2
    with SASession(engine) as s:
        tasks = s.query(SeedingTask).all()
        assert all(t.status == "todo" and t.brief_ref is None for t in tasks)
        _assert_all_pre_placement(tasks)


def test_budget_caps_task_count_by_priority(engine: Engine) -> None:
    with SASession(engine) as s:
        _seed_brand(s)
    with SASession(engine) as s:
        count = run_seeding_discovery(
            session=s, tenant_id=TENANT, brand_id=BRAND, since=SINCE, until=UNTIL,
            source_map=FakeSourceMap(), channels=ChannelCatalog.default(), budget=1,
        )
    assert count == 1
    with SASession(engine) as s:
        tasks = s.query(SeedingTask).all()
        assert len(tasks) == 1
        assert tasks[0].channel == "reddit"  # highest priority survives the budget cap


def test_missing_or_cross_tenant_brand_is_a_noop(engine: Engine) -> None:
    with SASession(engine) as s:
        _seed_brand(s)
    with SASession(engine) as s:
        assert run_seeding_discovery(
            session=s, tenant_id=TENANT, brand_id="nope", since=SINCE, until=UNTIL,
            source_map=FakeSourceMap(), channels=ChannelCatalog.default(),
        ) == 0
        assert run_seeding_discovery(
            session=s, tenant_id="other", brand_id=BRAND, since=SINCE, until=UNTIL,
            source_map=FakeSourceMap(), channels=ChannelCatalog.default(),
        ) == 0
    with SASession(engine) as s:
        assert s.query(SeedingTask).count() == 0


def test_job_owns_session_and_persists_todo_tasks(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # run_seeding_discovery_job opens its OWN session, wires the REAL CitationSourceMap, and with no
    # LLM key skips briefing -> hermetic. A competitor-only reddit citation yields a gap -> a target.
    db_path = tmp_path / "seed.db"
    url = f"sqlite:///{db_path}"
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    with SASession(eng) as s:
        _seed_brand(s)
        s.add(Prompt(id="p1", tenant_id=TENANT, brand_id=BRAND, text="best CRM", geo="us"))
        s.commit()
        s.add(
            ProbeRun(id="pr1", tenant_id=TENANT, prompt_id="p1", engine="perplexity", geo="us",
                     persona=None, ts=_IN_WINDOW, status="ok")
        )
        s.commit()
        s.add(
            AnswerExtraction(id="ae1", tenant_id=TENANT, probe_run_id="pr1", brand_mentioned=False,
                             position=None, sentiment="neutral",
                             cited_urls=["https://reddit.com/r/x/1"], competitors_present=["Beta"])
        )
        s.commit()
        seed_channels(s)
        s.commit()

    # No LLM configured -> briefing is skipped, so the job never calls a gateway.
    monkeypatch.setattr(
        trigger, "get_settings", lambda: Settings(database_url=url, llm_gateway="direct")
    )
    count = run_seeding_discovery_job(tenant_id=TENANT, brand_id=BRAND, since=SINCE, until=UNTIL)
    assert count >= 1

    with SASession(eng) as s:
        tasks = s.query(SeedingTask).all()
        assert tasks and any(t.channel == "reddit" for t in tasks)
        for t in tasks:
            assert t.status == "todo"
            assert t.compliance_report == {}
            assert t.compliance_status == "pending"
            assert t.placed_url is None


def test_llm_configured_recognizes_local_claude() -> None:
    # local_claude needs no key (Claude Max subscription) -> briefing is enabled; direct w/o a key
    # is not; portkey needs its key. Mirrors content.gateway.build_llm_client's routing.
    assert trigger._llm_configured(Settings(llm_gateway="local_claude"))
    assert not trigger._llm_configured(Settings(llm_gateway="direct", anthropic_api_key=""))
    assert trigger._llm_configured(Settings(llm_gateway="direct", anthropic_api_key="a"))
    assert trigger._llm_configured(Settings(llm_gateway="portkey", portkey_api_key="pk"))
    assert not trigger._llm_configured(Settings(llm_gateway="portkey", portkey_api_key=""))
