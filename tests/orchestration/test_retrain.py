"""Retrain trigger tests (m4-design §3.1, PRD §6.6, docs/tasks/M4-T12-retrain-trigger.md).

Hermetic (TRD §12): an in-memory SQLite session and a fake `Retrainer` -- no live model
training, no live data pull. Covers the spec's three scenarios (breach -> job created + flag
cleared, idempotent re-trigger, `poll` over flagged breaches) plus the required failure-path
counterpart: a `retrainer` that raises leaves the job `"failed"` and the triggering event's
`retrain_flag` untouched, so an operator sees the failure instead of it being silently retried.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.db import Base, DriftEvent
from gw_geo.common.db import RetrainJob as RetrainJobRow
from gw_geo.orchestration.retrain import RetrainTrigger


class FakeRetrainer:
    """Always "succeeds" with a deterministic model ref + metrics -- no real training."""

    def __init__(self) -> None:
        self.calls = 0

    def retrain(self, *, engine: str) -> dict[str, Any]:
        self.calls += 1
        return {"model_ref": f"s3://models/{engine}/v2", "metrics": {"auc": 0.81}}


class RaisingRetrainer:
    """Simulates a training-infra failure (e.g. the trainer's compute backend is down)."""

    def __init__(self) -> None:
        self.calls = 0

    def retrain(self, *, engine: str) -> dict[str, Any]:
        self.calls += 1
        raise RuntimeError("training infra unavailable")


def _session_with_breach() -> Session:
    """One breached+flagged `drift_event` (`d1`, engine `perplexity`) in a fresh SQLite DB.

    `ts` is required (`DriftEvent.ts` is a non-nullable column with no default) even though the
    task spec's fixture omits it -- filled in here so the row can actually be committed.
    """
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    s.add(
        DriftEvent(
            id="d1",
            engine="perplexity",
            canary_id="c1",
            baseline_rate=0.6,
            observed_rate=0.3,
            drop=0.3,
            breached=True,
            retrain_flag=True,
            ts=datetime.now(timezone.utc),
        )
    )
    s.commit()
    return s


def test_on_breach_creates_job_and_clears_flag() -> None:
    s = _session_with_breach()
    r = FakeRetrainer()
    job = RetrainTrigger(s, retrainer=r).on_breach("d1")
    assert job.status == "succeeded"
    assert job.model_ref is not None and job.model_ref.endswith("v2")
    assert job.metrics_after["auc"] == 0.81
    assert r.calls == 1
    retrieved = s.get(DriftEvent, "d1")
    assert retrieved is not None
    assert retrieved.retrain_flag is False


def test_on_breach_is_idempotent() -> None:
    s = _session_with_breach()
    r = FakeRetrainer()
    trig = RetrainTrigger(s, retrainer=r)
    j1 = trig.on_breach("d1")
    j2 = trig.on_breach("d1")
    assert j1.id == j2.id
    assert r.calls == 1
    assert s.query(RetrainJobRow).count() == 1


def test_poll_handles_all_flagged_breaches() -> None:
    s = _session_with_breach()
    jobs = RetrainTrigger(s, retrainer=FakeRetrainer()).poll()
    assert len(jobs) == 1
    assert jobs[0].model_engine == "perplexity"


def test_on_breach_marks_failed_and_keeps_flag_when_retrainer_raises() -> None:
    """Required failure-path counterpart: a raising `Retrainer` must not crash `on_breach`,
    must not clear the event's `retrain_flag`, and must still record exactly one `retrain_job`
    (status `"failed"`) so the breach is not silently lost.
    """
    s = _session_with_breach()
    r = RaisingRetrainer()
    job = RetrainTrigger(s, retrainer=r).on_breach("d1")
    assert job.status == "failed"
    assert r.calls == 1
    retrieved = s.get(DriftEvent, "d1")
    assert retrieved is not None
    assert retrieved.retrain_flag is True
    assert s.query(RetrainJobRow).count() == 1
