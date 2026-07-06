"""Local, in-process adaptation cycle job (m4-design §3.3, M5 live wiring) -- the LOCAL analogue of
`handlers/run_adaptation.py`, with **no** cloud/Lambda/EventBridge anywhere.

`orchestration.scheduler.run_adaptation_cycle` takes every collaborator injected by design, so the
same function serves both its unit suite and a real runner. `handlers/run_adaptation.py` is the
scheduled (EventBridge cron -> Lambda) runner; its production path shipped two placeholders -- an
`_UnwiredRetrainer` that raised and an `_unwired_discovery` that returned no targets -- because no
real `Retrainer`/`SourceMap` existed yet. This job is the local runner that wires the now-real
collaborators end-to-end:

* **drift** -- the M1 drift canary (`run_drift_canary`) bound to the configured engines/date, run
  via `asyncio.run` (mirrors the handler).
* **retrain** -- gated on `settings.retrain_on_breach` (m4-design §3.1): enabled (default) -> a real
  `RetrainTrigger` over the now-real `RankingRetrainer` (M5), so a breach actually retrains the
  affected engine's ranking model; disabled -> a no-op poller (the cycle still runs its
  drift/discovery/placement steps).
* **discovery** -- real `discover_targets` over the AnswerExtraction-backed `CitationSourceMap` and
  the persisted active channel catalog.
* **workflow** -- a real, tenant-scoped `SeedingWorkflow` over the default compliance ruleset. The
  cycle only ever `create()`s `todo` tasks (new human-facing work); it never runs compliance or
  places -- the white-hat, human-in-the-loop gate (PRD NG1) is untouched here.
* **bandit policy** -- the configured `UCB1Policy`/`ThompsonPolicy`, used to order discovered
  targets best-channel-first (the scheduler keeps its current simple budget-cap ranking; adopting
  `effort.allocate_effort` is a documented follow-on).

`get_settings` / `build_runtime` / `run_adaptation_cycle` / `run_drift_canary` /
`run_ranking_refresh_job` are imported by name so tests can patch them on this module and keep the
job hermetic. The job owns (and always closes) its own `Session`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.config import Settings, get_settings
from gw_geo.common.wiring import build_runtime
from gw_geo.orchestration.bandit import BanditPolicy, ThompsonPolicy, UCB1Policy
from gw_geo.orchestration.drift import DriftResult, run_drift_canary
from gw_geo.orchestration.ranking_gen import run_ranking_refresh_job
from gw_geo.orchestration.retrain import RetrainTrigger
from gw_geo.orchestration.retrainer import RankingRetrainer
from gw_geo.orchestration.scheduler import CycleResult, RetrainPoller, run_adaptation_cycle
from gw_geo.seeding.channels import load_catalog
from gw_geo.seeding.compliance import ComplianceEngine
from gw_geo.seeding.discovery import SeedingTarget, discover_targets
from gw_geo.seeding.sourcemap import CitationSourceMap
from gw_geo.seeding.workflow import SeedingWorkflow

logger = logging.getLogger(__name__)

# Default discovery look-back when the caller names no window: the trailing 90 days (inclusive) --
# the same convention as `seeding.trigger` / `attribution.trigger`.
_LOOKBACK_DAYS = 90
# Default per-cycle placement budget (new human-facing seeding tasks spawned per run) -- a rate
# limit on new work, not a target to fill (see `run_adaptation_cycle`).
_DEFAULT_BUDGET = 5


class _NoRetrainPoller:
    """No-op `RetrainPoller` for when `settings.retrain_on_breach` is disabled (m4-design §3.1)."""

    def poll(self) -> list[Any]:
        return []


def _build_retrain_poller(
    session: Session, settings: Settings, *, tenant_id: str, brand_id: str
) -> RetrainPoller:
    """The retrain poller, gated on `settings.retrain_on_breach`.

    Enabled (default): a real `RetrainTrigger` over the real `RankingRetrainer` (constructed with
    `run_ranking_refresh_job` by name, so a test patching this module's `run_ranking_refresh_job`
    reaches the retrainer). Disabled: a `_NoRetrainPoller`.
    """
    if not settings.retrain_on_breach:
        logger.info("adaptation cycle: retrain_on_breach disabled; skipping retrain trigger")
        return _NoRetrainPoller()
    return RetrainTrigger(
        session, retrainer=RankingRetrainer(tenant_id, brand_id, retrain_fn=run_ranking_refresh_job)
    )


def _build_bandit_policy(settings: Settings) -> BanditPolicy:
    """The configured `BanditPolicy`: `settings.bandit_policy` selects UCB1 vs. Thompson."""
    if settings.bandit_policy == "thompson":
        return ThompsonPolicy()
    return UCB1Policy(c=settings.bandit_explore_c)


def _default_window() -> tuple[str, str]:
    """`(since, until)` ISO dates for the default trailing `_LOOKBACK_DAYS`-day window."""
    until = datetime.now(timezone.utc).date()
    since = until - timedelta(days=_LOOKBACK_DAYS - 1)
    return since.isoformat(), until.isoformat()


def run_adaptation_job(
    *,
    tenant_id: str,
    brand_id: str,
    since: str | None = None,
    until: str | None = None,
    budget: int | None = None,
    date: str | None = None,
) -> CycleResult:
    """Run one measure -> sense -> adapt cycle for `(tenant_id, brand_id)` locally (m4-design §3.3).

    Wires the real drift canary, retrain trigger (gated on `settings.retrain_on_breach`, over the
    real `RankingRetrainer`), target discovery (over `CitationSourceMap`), seeding workflow, and
    bandit policy into `run_adaptation_cycle`. `since`/`until` default to the trailing
    `_LOOKBACK_DAYS`-day window; `budget` to `_DEFAULT_BUDGET`; `date` to today (UTC). Opens (and
    always closes) its own `Session`. Returns the cycle's `CycleResult`.
    """
    settings = get_settings()
    runtime = build_runtime(settings)

    default_since, default_until = _default_window()
    resolved_since = since if since is not None else default_since
    resolved_until = until if until is not None else default_until
    resolved_date = date if date is not None else datetime.now(timezone.utc).date().isoformat()
    resolved_budget = budget if budget is not None else _DEFAULT_BUDGET

    session = Session(create_engine(settings.database_url))
    try:

        def _drift_runner() -> list[DriftResult]:
            return asyncio.run(
                run_drift_canary(
                    session,
                    engines=list(runtime["engines"]),
                    threshold=settings.drift_threshold,
                    extractor=runtime["extractor"],
                    archive=runtime["archive"],
                    date=resolved_date,
                )
            )

        def _discovery() -> list[SeedingTarget]:
            return discover_targets(
                CitationSourceMap(session),
                tenant_id=tenant_id,
                brand_id=brand_id,
                since=resolved_since,
                until=resolved_until,
                channels=load_catalog(session),
            )

        result = run_adaptation_cycle(
            session,
            tenant_id=tenant_id,
            brand_id=brand_id,
            since=resolved_since,
            until=resolved_until,
            drift_runner=_drift_runner,
            retrain_trigger=_build_retrain_poller(
                session, settings, tenant_id=tenant_id, brand_id=brand_id
            ),
            discovery=_discovery,
            workflow=SeedingWorkflow(
                session, tenant_id, ComplianceEngine(ComplianceEngine.default_ruleset())
            ),
            bandit_policy=_build_bandit_policy(settings),
            budget=resolved_budget,
            date=resolved_date,
        )
    finally:
        session.close()

    logger.info(
        "adaptation job done tenant_id=%s brand_id=%s breaches=%d retrain_jobs=%d targets=%d "
        "tasks_spawned=%d",
        tenant_id,
        brand_id,
        result.drift_breaches,
        len(result.retrain_jobs),
        result.targets_found,
        result.tasks_spawned,
    )
    return result


__all__ = ["run_adaptation_job"]
