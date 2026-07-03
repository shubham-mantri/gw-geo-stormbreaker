"""AWS Lambda handler for the M4 continuous adaptation cycle (m4-design §3.3, T16).

Wraps `gw_geo.orchestration.scheduler.run_adaptation_cycle` (T15) -- measure -> sense -> adapt --
behind a scheduled Lambda (the `run_adaptation` function in `serverless.yml`, fired by an
EventBridge cron), mirroring `handlers/run_measurement.py` and `handlers/run_drift.py`: build real
collaborators from `Settings`/wiring, run the pipeline, return a JSON-safe body.

`run_adaptation_cycle` takes every collaborator as an injected parameter by design (its own module
docstring: this is how the same function serves both its unit-test suite and this real handler),
so this handler's only job is to build those collaborators two ways:

* **`deps` provided (tests):** used verbatim -- see `tests/handlers/test_adaptation_handler.py`.
  No live probe, training run, discovery scan, or placement ever runs in the default suite.
* **`deps=None` (production):** built from `Settings`/wiring. `drift_runner`, `workflow`, and
  `bandit_policy` are fully real: the M1 drift canary (the same `build_runtime` wiring
  `handlers/run_drift.py` uses), a real `seeding.workflow.SeedingWorkflow` (T10) over the default
  compliance ruleset (T03), and a `UCB1Policy`/`ThompsonPolicy` (T07) chosen by
  `settings.bandit_policy`. Two collaborators are honest, documented best-effort stand-ins rather
  than full cross-milestone integrations -- mirroring `common/wiring.py::_build_live_capture`'s
  "log and degrade gracefully" posture for a dependency that has nowhere real to resolve to yet:

  - `discovery` needs a `SourceMap` shaped like `seeding.discovery.SourceMap` (per-domain,
    you-vs-competitor citation share). `measurement/feed.py::citation_source_mix` (M1) answers a
    *different* question -- a `{source_type: fraction}` mix with no competitor comparison at all
    -- so there is no real `SourceMap` to wire yet. Feeding `discover_targets` that wrong-shaped
    dict would silently "work" (it would just always resolve to zero targets, since
    `discover_targets` reads a `"sources"` key no such dict has) while *looking* wired, which is
    worse than being explicit, so `_unwired_discovery` just returns no targets directly.
  - `retrain_trigger` is a **real** `orchestration.retrain.RetrainTrigger` -- all of its job
    bookkeeping/idempotency runs for real against the database -- but its injected `Retrainer`
    (satisfied in production by the M3 ranking trainer) is not yet adapted to the
    `Retrainer.retrain(*, engine) -> {"model_ref", "metrics"}` contract this trigger expects.
    `_UnwiredRetrainer.retrain` raises, which `RetrainTrigger.on_breach` (T12) already turns into
    an honest `"failed"` job rather than a silently faked success -- exactly the standing
    operator-facing signal that method's own docstring describes for a failing retrainer.

Both stand-ins, and the per-(tenant, brand) scheduling gap they sit next to (a bare EventBridge
cron cannot itself enumerate every tenant/brand -- see `serverless.yml`), are tracked there as
follow-ons rather than guessed at here.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.config import Settings, get_settings
from gw_geo.common.wiring import build_runtime
from gw_geo.orchestration.bandit import BanditPolicy, ThompsonPolicy, UCB1Policy
from gw_geo.orchestration.drift import DriftResult, run_drift_canary
from gw_geo.orchestration.retrain import RetrainTrigger
from gw_geo.orchestration.scheduler import run_adaptation_cycle
from gw_geo.seeding.compliance import ComplianceEngine
from gw_geo.seeding.discovery import SeedingTarget
from gw_geo.seeding.workflow import SeedingWorkflow

logger = logging.getLogger(__name__)


class _UnwiredRetrainer:
    """Placeholder `orchestration.retrain.Retrainer` for the production (`deps=None`) handler path.

    See this module's docstring: the M3 ranking trainer is not yet adapted to the
    `Retrainer.retrain(*, engine) -> {"model_ref", "metrics"}` contract, so guessing at that
    adapter here would risk silently mis-training (or mis-reporting) a model. Raising instead lets
    the already-implemented `RetrainTrigger.on_breach` (T12) do exactly what it already does for a
    failing retrainer: mark the job `"failed"` and leave the triggering `drift_event.retrain_flag`
    set -- an honest signal for an operator, never a faked success.
    """

    def retrain(self, *, engine: str) -> dict[str, Any]:
        raise NotImplementedError(
            f"M3 ranking retrainer is not wired into the adaptation-cycle handler yet "
            f"(engine={engine!r}); wiring a real Retrainer is a documented follow-on to M4-T16."
        )


def _unwired_discovery() -> list[SeedingTarget]:
    """Placeholder `discovery` collaborator for the production (`deps=None`) handler path.

    See this module's docstring: no real `seeding.discovery.SourceMap` exists yet, so this
    reports zero seeding targets for the cycle rather than feeding `discover_targets` a
    wrong-shaped citation-mix dict.
    """
    logger.warning(
        "adaptation cycle: no SourceMap wired yet for target discovery; reporting 0 targets"
    )
    return []


def _build_bandit_policy(settings: Settings) -> BanditPolicy:
    """The configured `BanditPolicy` (T07): `settings.bandit_policy` selects UCB1 vs. Thompson."""
    if settings.bandit_policy == "thompson":
        return ThompsonPolicy()
    return UCB1Policy(c=settings.bandit_explore_c)


def handler(
    event: dict[str, Any], context: Any = None, *, deps: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Run one adaptation cycle for `(tenant_id, brand_id)` (m4-design §3.3).

    `event` keys: `tenant_id`, `brand_id`, `since`, `until`, `budget` (int-coercible); optionally
    `date` (`YYYY-MM-DD`, default: today, UTC). `context` is the Lambda context object, unused
    here. `deps` (optional): injected `{drift_runner, retrain_trigger, discovery, workflow,
    bandit_policy, session}` -- when provided (tests), used verbatim and nothing in this module is
    built or imported for real; when omitted (production), every collaborator is built from
    `Settings`/wiring per this module's docstring.

    Returns `{"statusCode": 200, "body": <CycleResult as a dict>}`.
    """
    tenant_id = event["tenant_id"]
    brand_id = event["brand_id"]
    since = event["since"]
    until = event["until"]
    budget = int(event["budget"])
    date: str = event.get("date") or datetime.now(timezone.utc).date().isoformat()

    if deps is not None:
        result = run_adaptation_cycle(
            deps["session"],
            tenant_id=tenant_id,
            brand_id=brand_id,
            since=since,
            until=until,
            drift_runner=deps["drift_runner"],
            retrain_trigger=deps["retrain_trigger"],
            discovery=deps["discovery"],
            workflow=deps["workflow"],
            bandit_policy=deps["bandit_policy"],
            budget=budget,
            date=date,
        )
        return {"statusCode": 200, "body": result.model_dump()}

    settings = get_settings()
    runtime = build_runtime(settings)
    engine = create_engine(settings.database_url)
    session = Session(engine)
    try:

        def _drift_runner() -> list[DriftResult]:
            return asyncio.run(
                run_drift_canary(
                    session,
                    engines=list(runtime["engines"]),
                    threshold=settings.drift_threshold,
                    extractor=runtime["extractor"],
                    archive=runtime["archive"],
                    date=date,
                )
            )

        result = run_adaptation_cycle(
            session,
            tenant_id=tenant_id,
            brand_id=brand_id,
            since=since,
            until=until,
            drift_runner=_drift_runner,
            retrain_trigger=RetrainTrigger(session, retrainer=_UnwiredRetrainer()),
            discovery=_unwired_discovery,
            workflow=SeedingWorkflow(
                session, tenant_id, ComplianceEngine(ComplianceEngine.default_ruleset())
            ),
            bandit_policy=_build_bandit_policy(settings),
            budget=budget,
            date=date,
        )
        return {"statusCode": 200, "body": result.model_dump()}
    finally:
        session.close()
