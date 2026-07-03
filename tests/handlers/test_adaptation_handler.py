"""Adaptation-cycle Lambda handler tests (m4-design §3.3, docs/tasks/M4-T16-handlers-serverless.md).

Hermetic (TRD §12): every collaborator is an injected fake passed via `deps`, so the handler never
builds `Settings`/wiring here -- no live drift probe, retrain job, discovery scan, or placement
ever runs in this suite. See `gw_geo.handlers.run_adaptation`'s module docstring for how the
`deps=None` production path builds its collaborators instead.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.db import Base
from gw_geo.handlers.run_adaptation import handler


class FakeRetrain:
    def poll(self) -> list[object]:
        return []


class ScriptedRetrainJob:
    id = "rj1"
    model_engine = "perplexity"


class ScriptedRetrain:
    def poll(self) -> list[ScriptedRetrainJob]:
        return [ScriptedRetrainJob()]


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def _base_deps(session: Session, *, retrain_trigger: object) -> dict[str, object]:
    return {
        "drift_runner": lambda: [],
        "retrain_trigger": retrain_trigger,
        "discovery": lambda: [],
        "workflow": type("W", (), {"create": lambda self, **kwargs: "st0"})(),
        "bandit_policy": None,
        "session": session,
    }


def test_adaptation_handler_runs_with_injected_deps() -> None:
    session = _session()
    deps = _base_deps(session, retrain_trigger=FakeRetrain())

    out = handler(
        {
            "tenant_id": "t1",
            "brand_id": "b1",
            "since": "a",
            "until": "b",
            "budget": 2,
            "date": "2026-07-02",
        },
        deps=deps,
    )

    assert out["statusCode"] == 200
    assert out["body"]["targets_found"] == 0


def test_adaptation_handler_surfaces_full_cycle_result() -> None:
    session = _session()
    deps = _base_deps(session, retrain_trigger=ScriptedRetrain())

    out = handler(
        {
            "tenant_id": "t1",
            "brand_id": "b1",
            "since": "a",
            "until": "b",
            "budget": 1,
            "date": "2026-07-02",
        },
        deps=deps,
    )

    assert out["statusCode"] == 200
    assert out["body"]["retrain_jobs"] == ["rj1"]
    assert any("retrain" in alert.lower() for alert in out["body"]["alerts"])


def test_adaptation_handler_defaults_date_when_omitted() -> None:
    session = _session()
    deps = _base_deps(session, retrain_trigger=FakeRetrain())

    out = handler(
        {"tenant_id": "t1", "brand_id": "b1", "since": "a", "until": "b", "budget": 0},
        deps=deps,
    )

    assert out["statusCode"] == 200
    assert out["body"]["tasks_spawned"] == 0
