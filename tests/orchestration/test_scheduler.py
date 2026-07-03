"""Adaptation-cycle scheduler tests (M4-T15, `docs/tasks/M4-T15-scheduler.md`).

Hermetic (TRD §12): `drift_runner`, `retrain_trigger`, `discovery`, and `workflow` are all fakes
(`bandit_policy` is `None` throughout -- these tests only need `discovery()`'s own priority
order). `run_adaptation_cycle` never runs a live drift probe, retrain job, discovery scan, or
placement; the only DB access is `RecordingWorkflow`'s own in-memory SQLite session, which mirrors
`SeedingWorkflow.create`'s call shape without importing it -- keeping this suite (and the module
it exercises) decoupled from the concrete drift/discovery/retrain/effort implementations.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.db import Base
from gw_geo.common.models import SourceType
from gw_geo.orchestration.scheduler import run_adaptation_cycle
from gw_geo.seeding.discovery import SeedingTarget


class _FakeDriftResult:
    """Minimal `DriftResult`-shaped fake -- only `.breached` is read by the scheduler."""

    def __init__(self, *, breached: bool) -> None:
        self.breached = breached


class ScriptedDriftRunner:
    """Zero-arg `drift_runner` callable returning a scripted list of drift results."""

    def __init__(self, results: list[_FakeDriftResult] | None = None) -> None:
        self._results = results if results is not None else []

    def __call__(self) -> list[_FakeDriftResult]:
        return list(self._results)


class _FakeRetrainJob:
    """Minimal `RetrainJob`-shaped fake -- only `.id`/`.model_engine` are read."""

    def __init__(self, *, job_id: str, model_engine: str) -> None:
        self.id = job_id
        self.model_engine = model_engine


class ScriptedRetrainTrigger:
    """`retrain_trigger` fake: `.poll()` returns a scripted job list -- no real training."""

    def __init__(self, jobs: list[_FakeRetrainJob] | None = None) -> None:
        self._jobs = jobs if jobs is not None else []

    def poll(self) -> list[_FakeRetrainJob]:
        return list(self._jobs)


class ScriptedDiscovery:
    """Zero-arg `discovery` callable returning a scripted list of `SeedingTarget`s."""

    def __init__(self, targets: list[SeedingTarget] | None = None) -> None:
        self._targets = targets if targets is not None else []

    def __call__(self) -> list[SeedingTarget]:
        return list(self._targets)


class RecordingWorkflow:
    """`workflow` fake: records every `create(...)` call -- no compliance gate, no real writes."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.created: list[tuple[str, str]] = []

    def create(
        self,
        *,
        brand_id: str,
        channel: str,
        target_url: str | None = None,
        content_asset_id: str | None = None,
    ) -> str:
        task_id = f"st{len(self.created)}"
        self.created.append((channel, task_id))
        return task_id


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


class _PreferBanditPolicy:
    """Fake `BanditPolicy`: ranks channels by a fixed preference list (unknowns last), so a test
    can prove `_order_targets_by_channel_rank` actually reordered targets under the policy."""

    def __init__(self, preferred: list[str]) -> None:
        self._preferred = preferred

    def rank(self, arms: list) -> list[str]:
        return sorted(
            (arm.key for arm in arms),
            key=lambda key: self._preferred.index(key)
            if key in self._preferred
            else len(self._preferred),
        )


def _reddit_target(*, priority: float = 0.6) -> SeedingTarget:
    return SeedingTarget(
        channel="reddit",
        source_type=SourceType.REDDIT,
        domain="reddit.com",
        engine="perplexity",
        gap_score=priority,
        priority=priority,
        rationale="gap",
    )


def test_cycle_spawns_tasks_and_reports() -> None:
    session = _session()

    result = run_adaptation_cycle(
        session,
        tenant_id="t1",
        brand_id="b1",
        since="a",
        until="b",
        drift_runner=ScriptedDriftRunner([_FakeDriftResult(breached=True)]),
        retrain_trigger=ScriptedRetrainTrigger(
            [_FakeRetrainJob(job_id="rj1", model_engine="perplexity")]
        ),
        discovery=ScriptedDiscovery([_reddit_target()]),
        workflow=RecordingWorkflow(session),
        bandit_policy=None,
        budget=3,
        date="2026-07-02",
    )

    assert result.drift_breaches == 1
    assert result.retrain_jobs == ["rj1"]
    assert result.targets_found == 1 and result.tasks_spawned >= 1
    assert any("retrain" in alert.lower() for alert in result.alerts)
    assert any(
        "reddit" in alert.lower() or "opportunit" in alert.lower() for alert in result.alerts
    )


def test_cycle_with_no_breaches_and_no_targets_is_empty() -> None:
    session = _session()

    result = run_adaptation_cycle(
        session,
        tenant_id="t1",
        brand_id="b1",
        since="a",
        until="b",
        drift_runner=ScriptedDriftRunner(),
        retrain_trigger=ScriptedRetrainTrigger(),
        discovery=ScriptedDiscovery(),
        workflow=RecordingWorkflow(session),
        bandit_policy=None,
        budget=5,
        date="2026-07-02",
    )

    assert result.drift_breaches == 0
    assert result.retrain_jobs == []
    assert result.targets_found == 0
    assert result.tasks_spawned == 0
    assert result.alerts == []


def test_budget_caps_tasks_spawned_below_targets_found() -> None:
    session = _session()
    workflow = RecordingWorkflow(session)
    targets = [
        _reddit_target(priority=0.9),
        SeedingTarget(
            channel="g2",
            source_type=SourceType.REVIEW_SITE,
            domain="g2.com",
            engine="perplexity",
            gap_score=0.4,
            priority=0.4,
            rationale="gap",
        ),
    ]

    result = run_adaptation_cycle(
        session,
        tenant_id="t1",
        brand_id="b1",
        since="a",
        until="b",
        drift_runner=ScriptedDriftRunner(),
        retrain_trigger=ScriptedRetrainTrigger(),
        discovery=ScriptedDiscovery(targets),
        workflow=workflow,
        bandit_policy=None,
        budget=1,
        date="2026-07-02",
    )

    assert result.targets_found == 2
    assert result.tasks_spawned == 1
    assert len(workflow.created) == 1
    assert workflow.created[0][0] == "reddit"  # higher-priority target wins the single slot


def test_bandit_policy_reorders_targets_by_channel_rank() -> None:
    # Covers the `bandit_policy is not None` branch of _order_targets_by_channel_rank: discovery
    # order puts reddit first, but a policy preferring g2 flips the order the slots are spent in.
    session = _session()
    workflow = RecordingWorkflow(session)
    targets = [
        _reddit_target(priority=0.9),
        SeedingTarget(
            channel="g2",
            source_type=SourceType.REVIEW_SITE,
            domain="g2.com",
            engine="perplexity",
            gap_score=0.4,
            priority=0.4,
            rationale="gap",
        ),
    ]

    result = run_adaptation_cycle(
        session,
        tenant_id="t1",
        brand_id="b1",
        since="a",
        until="b",
        drift_runner=ScriptedDriftRunner(),
        retrain_trigger=ScriptedRetrainTrigger(),
        discovery=ScriptedDiscovery(targets),
        workflow=workflow,
        bandit_policy=_PreferBanditPolicy(["g2", "reddit"]),
        budget=2,
        date="2026-07-02",
    )

    assert result.tasks_spawned == 2
    assert [channel for channel, _ in workflow.created] == ["g2", "reddit"]
