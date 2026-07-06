"""Seeding-effort reward reconcile (m4-design §3.2, PRD §10, M5 live wiring).

Rewards for the seeding-effort bandit (`orchestration.effort`) arrive **delayed**: a placement's
real payoff is not "was it posted" but "did engines start citing the brand because of it", which
takes ~14-21 days to show up in the citation-source mix (PRD §10). This job is that delayed
reward path: it scans off-site placements old enough to have had an effect and feeds each channel's
observed **corroboration lift** back onto its bandit arm, so the bandit accumulates real signal
about which channels actually move corroboration.

Per aged `placed` `seeding_task` (for one tenant/brand):

1. **aging gate.** Only tasks whose last update is at least `aging_days` old are reconciled -- a
   just-placed task has not had time to be picked up, so rewarding it now would be noise. (There is
   no `placed_at` column, so `updated_at` -- the last state change -- is the aging reference; see
   CONCERNS.)
2. **corroboration.** `corroboration.update_corroboration` recomputes and persists the brand's
   corroboration count over `[placement_date, now]` (and advances `placed -> corroborated` once any
   independent domain corroborates).
3. **reward = lift, clamped [0, 1].** `lift = new_count - previously_recorded_count`; the reward is
   that lift clamped to `[0.0, 1.0]` (a placement that yields at least one new independent
   corroborating domain is a full-credit win; none is a zero). `effort.record_reward` upserts it
   onto arm `f"{channel}:default"`.

The corroboration signal is read through the injected `SourceMap` protocol (T05) -- a fake in tests,
the AnswerExtraction-backed `CitationSourceMap` in the job -- so the core is hermetic (no live DB
source / network). `run_reward_reconcile_job` is the local, in-process job that owns its own session
and wires that real `SourceMap`; `get_settings` is imported by name for patchability. No cloud, no
poster, white-hat.

This wave records rewards so the bandit **accumulates**; actually spending the accumulated ranking
via `effort.allocate_effort` in the scheduler (which today keeps its simpler budget-cap ranking) is
a documented follow-on, not this job.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.config import get_settings
from gw_geo.common.db import SeedingTask, TenantScopedSession
from gw_geo.orchestration.effort import record_reward
from gw_geo.seeding.corroboration import update_corroboration
from gw_geo.seeding.discovery import SourceMap
from gw_geo.seeding.sourcemap import CitationSourceMap
from gw_geo.seeding.workflow import SeedingStatus

logger = logging.getLogger(__name__)

# Default aging window before a placement's corroboration is treated as a signal (PRD §10: rewards
# arrive 14-21 days after a placement). 14d is the lower bound -- the earliest a placement could
# plausibly have moved the citation mix.
_DEFAULT_AGING_DAYS = 14

_ARM_VARIANT = "default"


def _reward_from_lift(lift: int) -> float:
    """Normalize a corroboration `lift` (new independent domains) to a reward in `[0, 1]`."""
    return min(max(float(lift), 0.0), 1.0)


def run_reward_reconcile(
    *,
    session: Session,
    tenant_id: str,
    brand_id: str,
    source_map: SourceMap,
    aging_days: int = _DEFAULT_AGING_DAYS,
    now: datetime | None = None,
) -> int:
    """Reconcile corroboration rewards for `brand_id`'s aged `placed` seeding tasks (hermetic core).

    `source_map` (the citation-source reader) is injected. For every `placed` task at least
    `aging_days` old, recompute corroboration (`update_corroboration`), derive the reward from the
    corroboration lift (clamped `[0, 1]`), and record it onto arm `f"{channel}:default"` via
    `effort.record_reward`. `now` (the "as of" instant, default: now UTC) drives both the aging
    cutoff and the corroboration window's `until`. Returns the number of tasks reconciled.
    """
    resolved_now = now if now is not None else datetime.now(timezone.utc)
    cutoff = resolved_now - timedelta(days=aging_days)
    until = resolved_now.date().isoformat()

    scoped = TenantScopedSession(session, tenant_id)
    tasks = (
        scoped.query(SeedingTask)
        .filter(
            SeedingTask.brand_id == brand_id,
            SeedingTask.status == SeedingStatus.PLACED.value,
        )
        .all()
    )

    reconciled = 0
    for task in tasks:
        updated = task.updated_at
        if updated is None:
            continue
        if updated.tzinfo is None:  # sqlite may return naive UTC; normalize before comparing
            updated = updated.replace(tzinfo=timezone.utc)
        if updated > cutoff:
            continue  # not aged enough yet -- rewarding now would be noise

        old_count = task.corroboration_count
        since = updated.date().isoformat()
        new_count = update_corroboration(
            session, source_map, tenant_id=tenant_id, task_id=task.id, since=since, until=until
        )
        reward = _reward_from_lift(new_count - old_count)
        record_reward(
            session,
            tenant_id=tenant_id,
            brand_id=brand_id,
            arm_key=f"{task.channel}:{_ARM_VARIANT}",
            reward=reward,
        )
        reconciled += 1
        logger.info(
            "reward reconcile tenant_id=%s brand_id=%s task=%s channel=%s lift=%d reward=%.3f",
            tenant_id,
            brand_id,
            task.id,
            task.channel,
            new_count - old_count,
            reward,
        )

    return reconciled


def run_reward_reconcile_job(
    *, tenant_id: str, brand_id: str, aging_days: int = _DEFAULT_AGING_DAYS
) -> int:
    """Local, in-process reward reconcile for `brand_id`; opens (and always closes) its own session.

    The single unit both the CLI `reward-reconcile` subcommand and any future request path call.
    Owns its `Session` (from `settings.database_url`) and wires the real AnswerExtraction-backed
    `CitationSourceMap`. No AWS/Lambda/EventBridge, no poster. Returns the number of tasks
    reconciled. `get_settings` is imported by name so tests can patch it here.
    """
    settings = get_settings()
    session = Session(create_engine(settings.database_url))
    try:
        count = run_reward_reconcile(
            session=session,
            tenant_id=tenant_id,
            brand_id=brand_id,
            source_map=CitationSourceMap(session),
            aging_days=aging_days,
        )
    finally:
        session.close()

    logger.info(
        "reward reconcile job done tenant_id=%s brand_id=%s reconciled=%d",
        tenant_id,
        brand_id,
        count,
    )
    return count


__all__ = ["run_reward_reconcile", "run_reward_reconcile_job"]
