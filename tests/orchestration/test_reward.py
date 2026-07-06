"""Tests for `orchestration.reward` -- the seeding-effort reward reconcile (M5 live wiring).

Hermetic (TRD §12): the corroboration signal is read through an injected fake `SourceMap` (no live
AnswerExtraction/probe data); the bandit arm is persisted to in-memory SQLite with FK enforcement
ON (Tenant -> Brand -> SeedingTask/bandit_arm_effort seeded parents-first).

The reward = corroboration *lift* (new count - previously recorded), clamped to [0, 1], recorded
onto the effort bandit arm `f"{channel}:default"` so the bandit accumulates -- exactly the delayed,
one-observation-per-call reward path `effort.record_reward` documents. Post-M5, a reward is recorded
**only when the corroboration advances** (lift > 0): a still-`placed`, uncorroborated task is not
re-rewarded 0 on every run, so its pull count can't be inflated.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from gw_geo.common.config import Settings
from gw_geo.common.db import Base, Brand, EffortBanditArm, SeedingTask, Tenant
from gw_geo.orchestration import reward as reward_mod
from gw_geo.orchestration.reward import run_reward_reconcile, run_reward_reconcile_job

TENANT = "t1"
BRAND = "b1"
_NOW = datetime(2026, 7, 6, tzinfo=timezone.utc)


class FakeSourceMap:
    """Reports `domains` as corroborating (you_pct>0, independent) the brand."""

    def __init__(self, domains: list[str]) -> None:
        self._domains = domains

    def citation_source_mix(self, *, tenant_id, brand_id, since, until):
        return {"sources": [
            {"domain": d, "source_type": "reddit", "engine": "perplexity", "you_pct": 0.5}
            for d in self._domains
        ]}


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = Session(engine)
    s.add(Tenant(id=TENANT, name="t", sampling_budget_daily=100.0))
    s.add(Brand(id=BRAND, tenant_id=TENANT, name="b", domain="b.com"))
    s.commit()
    return s


def _placed_task(s: Session, *, task_id: str, channel: str, age_days: int,
                 status: str = "placed", corroboration_count: int = 0) -> None:
    aged = _NOW - timedelta(days=age_days)
    s.add(SeedingTask(id=task_id, tenant_id=TENANT, brand_id=BRAND, channel=channel,
                      status=status, compliance_status="passed",
                      corroboration_count=corroboration_count, created_at=aged, updated_at=aged))
    s.commit()


def _arm(s: Session, arm_key: str) -> EffortBanditArm | None:
    return (
        s.query(EffortBanditArm)
        .filter_by(tenant_id=TENANT, brand_id=BRAND, arm_key=arm_key)
        .one_or_none()
    )


def test_rewards_aged_placed_task_with_corroboration_lift() -> None:
    s = _session()
    _placed_task(s, task_id="st1", channel="reddit", age_days=30)

    n = run_reward_reconcile(
        session=s, tenant_id=TENANT, brand_id=BRAND,
        source_map=FakeSourceMap(["reddit.com", "g2.com"]), aging_days=14, now=_NOW,
    )

    assert n == 1
    arm = _arm(s, "reddit:default")
    assert arm is not None
    assert arm.pulls == 1
    assert arm.reward_sum == 1.0  # lift of 2 domains clamped to [0,1]
    task = s.get(SeedingTask, "st1")
    assert task.corroboration_count == 2
    assert task.status == "corroborated"  # placed -> corroborated once corroboration > 0


def test_no_reward_recorded_when_corroboration_does_not_advance() -> None:
    # M5 review: a still-`placed`, uncorroborated task must NOT be re-rewarded 0 on every run --
    # recording a reward-0 pull each reconcile would inflate the arm's pull count and bias the
    # bandit. No advance (lift 0) -> no reward, no pull, no arm row, and it stays `placed`.
    s = _session()
    _placed_task(s, task_id="st1", channel="quora", age_days=30)

    n = run_reward_reconcile(
        session=s, tenant_id=TENANT, brand_id=BRAND,
        source_map=FakeSourceMap([]), aging_days=14, now=_NOW,
    )

    assert n == 0
    assert _arm(s, "quora:default") is None  # no pull recorded for a non-advancing task
    assert s.get(SeedingTask, "st1").status == "placed"  # no corroboration -> not advanced


def test_uncorroborated_task_is_not_re_rewarded_across_runs() -> None:
    # The bias the fix targets: repeated reconcile runs over the same never-corroborated task must
    # never accumulate pulls. Three runs, still no corroboration -> arm still absent (0 pulls).
    s = _session()
    _placed_task(s, task_id="st1", channel="quora", age_days=30)

    for _ in range(3):
        run_reward_reconcile(
            session=s, tenant_id=TENANT, brand_id=BRAND,
            source_map=FakeSourceMap([]), aging_days=14, now=_NOW,
        )

    assert _arm(s, "quora:default") is None


def test_skips_task_not_yet_aged() -> None:
    s = _session()
    _placed_task(s, task_id="st1", channel="reddit", age_days=2)  # < aging_days

    n = run_reward_reconcile(
        session=s, tenant_id=TENANT, brand_id=BRAND,
        source_map=FakeSourceMap(["reddit.com"]), aging_days=14, now=_NOW,
    )

    assert n == 0
    assert _arm(s, "reddit:default") is None


def test_skips_non_placed_tasks() -> None:
    s = _session()
    _placed_task(s, task_id="st1", channel="reddit", age_days=30, status="ready_for_human")

    n = run_reward_reconcile(
        session=s, tenant_id=TENANT, brand_id=BRAND,
        source_map=FakeSourceMap(["reddit.com"]), aging_days=14, now=_NOW,
    )

    assert n == 0
    assert _arm(s, "reddit:default") is None


def test_run_reward_reconcile_job_owns_session_and_wires_source_map(tmp_path, monkeypatch) -> None:
    # The job opens its own session from settings.database_url and wires the real CitationSourceMap.
    # With no AnswerExtraction data seeded, corroboration stays 0 -> the placement does not advance,
    # so (post-M5) NO reward is recorded and no arm row is created. This still exercises the job's
    # full wiring end-to-end (own session + real CitationSourceMap + the advance-guarded reward path)
    # and proves the re-sweep guard holds through the real source map, not just the injected fake.
    url = f"sqlite:///{tmp_path / 'reward.db'}"
    eng = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(Tenant(id=TENANT, name="t", sampling_budget_daily=100.0))
        s.add(Brand(id=BRAND, tenant_id=TENANT, name="b", domain="b.com"))
        s.commit()
        aged = _NOW - timedelta(days=40)
        s.add(SeedingTask(id="st1", tenant_id=TENANT, brand_id=BRAND, channel="reddit",
                          status="placed", compliance_status="passed", corroboration_count=0,
                          created_at=aged, updated_at=aged))
        s.commit()

    monkeypatch.setattr(reward_mod, "get_settings", lambda: Settings(database_url=url))
    n = run_reward_reconcile_job(tenant_id=TENANT, brand_id=BRAND, aging_days=14)

    assert n == 0  # no corroboration advance -> no reward observation recorded
    with Session(eng) as s:
        arm = s.query(EffortBanditArm).filter_by(
            tenant_id=TENANT, brand_id=BRAND, arm_key="reddit:default"
        ).one_or_none()
        assert arm is None  # no spurious reward-0 pull for a non-advancing task
