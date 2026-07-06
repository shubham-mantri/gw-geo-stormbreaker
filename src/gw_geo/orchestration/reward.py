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
3. **reward on advance only.** `lift = new_count - previously_recorded_count`. A reward is recorded
   **only when `lift > 0`** -- i.e. the placement actually gained at least one new independent
   corroborating domain (the `placed -> corroborated` transition). That reward is the lift clamped
   to `[0.0, 1.0]` (any new domain is a full-credit win), upserted onto arm `f"{channel}:default"`
   via `effort.record_reward`. A still-`placed`, uncorroborated task (`lift <= 0`) is left alone:
   recording a reward-0 pull for it every run would bias the bandit's pull counts (M5 review). A
   corroborated task flips out of the `placed` scan, so its single win is recorded exactly once.

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
    `aging_days` old, recompute corroboration (`update_corroboration`); when the corroboration
    **advanced** this run (lift > 0), record the reward (lift clamped `[0, 1]`) onto arm
    `f"{channel}:default"` via `effort.record_reward`. A task whose corroboration did not advance is
    skipped -- no reward, no pull -- so a never-corroborated placement is not re-rewarded 0 on every
    run (M5 review). `now` (the "as of" instant, default: now UTC) drives both the aging cutoff and
    the corroboration window's `until`. Returns the number of tasks for which a reward was recorded
    (i.e. whose corroboration advanced), not merely the number scanned.
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
        lift = new_count - old_count
        # M5 review: record a reward ONLY when corroboration actually advanced this run (lift > 0 --
        # the placed->corroborated transition / new independent domains). The aging gate is a lower
        # bound only, so a still-`placed`, never-corroborated task is otherwise re-swept on every
        # reconcile run and re-recorded with reward 0, inflating that arm's pull count and biasing
        # `allocate_effort` once it is adopted. A task whose corroboration advances flips to
        # `corroborated` (via `update_corroboration`) and so drops out of the `status == placed`
        # query on the next run -- its reward is recorded exactly once (corroborated-once preserved).
        if lift <= 0:
            continue

        reward = _reward_from_lift(lift)
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
            lift,
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
