"""Tests for the local adaptation-cycle job (`orchestration.adaptation_trigger`, M5 live wiring).

Hermetic (TRD §12): the first three tests patch `run_adaptation_cycle` (imported by name) to
capture how the job wires its collaborators; the last runs the *real* cycle over an empty DB with
the drift canary stubbed -- proving the real collaborators (RetrainTrigger+RankingRetrainer,
SeedingWorkflow, discover_targets over CitationSourceMap, the bandit policy) compose without any
live probe/train/placement. No cloud, no EventBridge.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.config import Settings
from gw_geo.common.db import Base, Brand, Tenant
from gw_geo.orchestration import adaptation_trigger
from gw_geo.orchestration.adaptation_trigger import _NoRetrainPoller, run_adaptation_job
from gw_geo.orchestration.bandit import UCB1Policy
from gw_geo.orchestration.retrain import RetrainTrigger
from gw_geo.orchestration.retrainer import RankingRetrainer
from gw_geo.orchestration.scheduler import CycleResult
from gw_geo.seeding.workflow import SeedingWorkflow


def _runtime() -> dict[str, object]:
    return {"engines": ["perplexity"], "extractor": object(), "archive": object()}


def test_wires_real_collaborators(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _spy(session, **kwargs):
        captured["session"] = session
        captured.update(kwargs)
        return CycleResult()

    monkeypatch.setattr(adaptation_trigger, "get_settings",
                        lambda: Settings(database_url="sqlite://", retrain_on_breach=True,
                                         bandit_policy="ucb1"))
    monkeypatch.setattr(adaptation_trigger, "build_runtime", lambda s: _runtime())
    monkeypatch.setattr(adaptation_trigger, "run_adaptation_cycle", _spy)

    out = run_adaptation_job(
        tenant_id="t1", brand_id="b1", since="2026-06-01", until="2026-06-30", budget=3
    )

    assert isinstance(out, CycleResult)
    assert isinstance(captured["session"], Session)  # job owns its own session
    assert captured["tenant_id"] == "t1" and captured["brand_id"] == "b1"
    assert captured["since"] == "2026-06-01" and captured["until"] == "2026-06-30"
    assert captured["budget"] == 3
    # Retrain trigger is a REAL RetrainTrigger over the REAL RankingRetrainer (not the placeholder).
    rt = captured["retrain_trigger"]
    assert isinstance(rt, RetrainTrigger)
    assert isinstance(rt._retrainer, RankingRetrainer)  # noqa: SLF001 (documenting real wiring)
    assert isinstance(captured["workflow"], SeedingWorkflow)
    assert isinstance(captured["bandit_policy"], UCB1Policy)
    assert callable(captured["drift_runner"]) and callable(captured["discovery"])


def test_honors_retrain_on_breach_disabled(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(adaptation_trigger, "get_settings",
                        lambda: Settings(database_url="sqlite://", retrain_on_breach=False))
    monkeypatch.setattr(adaptation_trigger, "build_runtime", lambda s: _runtime())
    monkeypatch.setattr(adaptation_trigger, "run_adaptation_cycle",
                        lambda session, **kw: captured.update(kw) or CycleResult())

    run_adaptation_job(tenant_id="t1", brand_id="b1")

    assert isinstance(captured["retrain_trigger"], _NoRetrainPoller)
    assert not isinstance(captured["retrain_trigger"], RetrainTrigger)


def test_defaults_window_budget_and_date(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(adaptation_trigger, "get_settings",
                        lambda: Settings(database_url="sqlite://"))
    monkeypatch.setattr(adaptation_trigger, "build_runtime", lambda s: _runtime())
    monkeypatch.setattr(adaptation_trigger, "run_adaptation_cycle",
                        lambda session, **kw: captured.update(kw) or CycleResult())

    run_adaptation_job(tenant_id="t1", brand_id="b1")

    assert len(captured["since"]) == 10 and len(captured["until"]) == 10  # YYYY-MM-DD defaults
    assert captured["budget"] == 5  # default per-cycle budget
    assert len(captured["date"]) == 10


def test_composes_real_cycle_end_to_end(tmp_path, monkeypatch) -> None:
    url = f"sqlite:///{tmp_path / 'adapt.db'}"
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(Tenant(id="t1", name="t", sampling_budget_daily=100.0))
        s.add(Brand(id="b1", tenant_id="t1", name="b", domain="b.com"))
        s.commit()

    monkeypatch.setattr(adaptation_trigger, "get_settings",
                        lambda: Settings(database_url=url, retrain_on_breach=True))
    monkeypatch.setattr(adaptation_trigger, "build_runtime", lambda s: _runtime())
    drift = AsyncMock(return_value=[])  # no live probes; no breaches
    monkeypatch.setattr(adaptation_trigger, "run_drift_canary", drift)

    out = run_adaptation_job(
        tenant_id="t1", brand_id="b1", since="2026-04-01", until="2026-07-06", budget=3
    )

    assert isinstance(out, CycleResult)
    assert out.drift_breaches == 0
    assert out.retrain_jobs == []
    assert out.targets_found == 0  # empty DB -> no discovery targets
    assert out.tasks_spawned == 0
    drift.assert_awaited_once()  # the real drift_runner invoked the (stubbed) canary
