"""Continuous-loop scheduler: one measure -> sense -> adapt cycle (m4-design §3.3, PRD §6.6).

`run_adaptation_cycle` sits at the top of the M4 self-adaptation loop: it runs the M1 drift
canary, fires retrain triggers on any breach, discovers off-site seeding targets, spends a fixed
per-cycle `budget` of new placement slots on the best of those targets (ranked by an injected
bandit policy), spawns a `todo` `seeding_task` per spent slot, and collects a flat list of
human-readable alerts (ui-spec §3.7: `"🔴 ..."` for a problem, `"🎯 ..."` for an opportunity).

This module is **pure orchestration**: every collaborator -- the drift runner, the retrain
trigger, the target-discovery scan, the seeding workflow, the bandit policy -- is injected as a
parameter, never imported or constructed here. That is load-bearing, not a style preference:

- It is how this is hermetically testable (TRD §12) despite chaining together four subsystems
  that each do real I/O in production (live engine probes, DB-backed retrain jobs, a citation-
  source scan, human-gated placement writes): every test in `tests/orchestration/test_scheduler.py`
  passes plain fakes and never touches a network, a model, or a live placement.
- It is how the same function serves both that unit-test suite and the real EventBridge-cron ->
  Lambda handler (T16): the handler builds real collaborators (`orchestration.drift.run_drift_canary`
  bound to today's engines/date, a real `orchestration.retrain.RetrainTrigger`,
  `seeding.discovery.discover_targets` bound to a live `SourceMap`, a real
  `seeding.workflow.SeedingWorkflow`) and this function never has to know the difference.

Collaborator *shapes* are declared as minimal local `Protocol`s (`RetrainPoller`,
`SeedingTaskCreator`, `DriftBreachLike`, `SeedingTargetLike`) instead of importing the concrete
`orchestration.drift` / `orchestration.retrain` / `orchestration.effort` / `seeding.discovery`
modules: this function only ever touches a handful of attributes/methods on each collaborator
(e.g. `.poll()`, `.create(...)`, `.breached`), so naming just that slice keeps this module
decoupled from those subsystems' concrete implementations -- any object with the right shape,
including a bare test double, works (see T16's own handler tests, which pass a
`type("W", (), {"create": ...})()` in place of a real `SeedingWorkflow`).
`orchestration.bandit`'s `Arm`/`BanditPolicy` are the one exception: they are the *pure*
arm-ranking data/policy contract (no I/O, no persistence -- `orchestration.effort` is the
persistence layer built on top of them, and is *not* imported here), already the shared contract
`orchestration.effort` (T14) itself imports rather than re-declaring, so doing the same here is
the same "depend on the pure math, not the service" move for exactly the same reason.

Sequence per cycle (m4-design §3.3):

1. `drift_runner()` -> a list of drift-canary results; `drift_breaches` counts `.breached`.
2. `retrain_trigger.poll()` -> any newly triggered retrain jobs; one `"🔴 retrain"` alert per job.
3. `discovery()` -> ranked `SeedingTarget`-shaped opportunities; `targets_found` is the raw count.
4. Targets are ordered best-channel-first under `bandit_policy` (see `_order_targets_by_channel_
   rank`); `budget` then caps how many of those (best-first) targets this cycle actually spends a
   new placement slot on -- `budget` is a per-cycle rate limit on new human-facing work, not a
   requirement to fill every slot.
5. Each spent slot spawns one `todo` `seeding_task` via `workflow.create(...)` and one `"🎯
   opportunity"` alert.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Protocol

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from gw_geo.orchestration.bandit import Arm, BanditPolicy

logger = logging.getLogger(__name__)


class CycleResult(BaseModel):
    """Summary of one `run_adaptation_cycle` run (m4-design §3.3)."""

    drift_breaches: int = 0
    retrain_jobs: list[str] = Field(default_factory=list)
    targets_found: int = 0
    tasks_spawned: int = 0
    alerts: list[str] = Field(default_factory=list)


class DriftBreachLike(Protocol):
    """Shape needed from one drift-canary result (`orchestration.drift.DriftResult`)."""

    breached: bool


class RetrainJobLike(Protocol):
    """Shape needed from one triggered retrain job (`orchestration.retrain.RetrainJob`)."""

    id: str
    model_engine: str


class RetrainPoller(Protocol):
    """Shape needed from a retrain trigger (`orchestration.retrain.RetrainTrigger`)."""

    def poll(self) -> Sequence[RetrainJobLike]: ...


class SeedingTargetLike(Protocol):
    """Shape needed from one discovered target (`seeding.discovery.SeedingTarget`)."""

    channel: str
    domain: str
    priority: float
    rationale: str


class SeedingTaskCreator(Protocol):
    """Shape needed from a seeding workflow (`seeding.workflow.SeedingWorkflow`)."""

    def create(
        self,
        *,
        brand_id: str,
        channel: str,
        target_url: str | None = None,
        content_asset_id: str | None = None,
    ) -> str: ...


DriftRunner = Callable[[], Sequence[DriftBreachLike]]
Discovery = Callable[[], Sequence[SeedingTargetLike]]


def _order_targets_by_channel_rank(
    targets: Sequence[SeedingTargetLike], bandit_policy: BanditPolicy | None
) -> list[SeedingTargetLike]:
    """Best-channel-first ordering of `targets` under `bandit_policy`.

    Builds one zero-stat `Arm` per distinct channel among `targets` -- this cycle has no
    persisted per-channel history to load (that accrual is `orchestration.effort`'s concern, a
    separate persistence layer not touched here) -- and ranks those channels via
    `bandit_policy.rank`. Targets are then stably sorted by their channel's rank, so targets
    sharing a channel keep their relative `discovery()` order (already priority-sorted, per
    `seeding.discovery.discover_targets`'s contract).

    `bandit_policy=None` (no policy configured) returns `targets` unchanged: `discovery()`'s own
    order stands as the priority order.
    """
    if bandit_policy is None or not targets:
        return list(targets)

    channels = list(dict.fromkeys(target.channel for target in targets))
    ranked_channels = bandit_policy.rank([Arm(key=channel) for channel in channels])
    rank_of = {channel: index for index, channel in enumerate(ranked_channels)}
    return sorted(targets, key=lambda target: rank_of.get(target.channel, len(ranked_channels)))


def run_adaptation_cycle(
    session: Session,
    *,
    tenant_id: str,
    brand_id: str,
    since: str,
    until: str,
    drift_runner: DriftRunner,
    retrain_trigger: RetrainPoller,
    discovery: Discovery,
    workflow: SeedingTaskCreator,
    bandit_policy: BanditPolicy | None,
    budget: int,
    date: str,
) -> CycleResult:
    """Run one measure -> sense -> adapt cycle for `(tenant_id, brand_id)` (m4-design §3.3).

    `session` is accepted for interface symmetry with the rest of this codebase's
    `(session, *, tenant_id, brand_id, ...)`-shaped entry points; every collaborator here is
    already bound to whatever session/date-range/tenant context it needs (e.g. a real
    `SeedingWorkflow` is constructed with its own tenant-scoped session, `drift_runner`/
    `discovery` are zero-arg closures already bound over `since`/`until`/`date`), so this
    function does not read or write through `session` directly -- it never runs a live
    measurement, training run, or placement itself.

    A non-positive `budget` spends zero slots (clamped, not an error): a temporarily
    misconfigured or exhausted budget should not stop this cycle's drift/retrain monitoring from
    running. See the module docstring for the full step-by-step sequence.

    Returns a `CycleResult` summarizing what happened; never raises for "nothing to do" -- a
    quiet cycle (no breaches, no jobs, no targets) yields an all-zero `CycleResult` with no
    alerts.
    """
    logger.info(
        "adaptation cycle start tenant_id=%s brand_id=%s since=%s until=%s date=%s budget=%d",
        tenant_id,
        brand_id,
        since,
        until,
        date,
        budget,
    )

    alerts: list[str] = []

    drift_results = list(drift_runner())
    drift_breaches = sum(1 for result in drift_results if result.breached)

    retrain_jobs = list(retrain_trigger.poll())
    for job in retrain_jobs:
        alerts.append(f"🔴 retrain triggered for {job.model_engine} (job {job.id})")

    targets = list(discovery())
    targets_found = len(targets)

    ordered_targets = _order_targets_by_channel_rank(targets, bandit_policy)
    allocated_targets = ordered_targets[: max(budget, 0)]

    tasks_spawned = 0
    for target in allocated_targets:
        workflow.create(brand_id=brand_id, channel=target.channel, target_url=target.domain)
        tasks_spawned += 1
        alerts.append(
            f"🎯 opportunity: {target.channel} gap on {target.domain} "
            f"(priority {target.priority:.2f}) -- {target.rationale}"
        )

    result = CycleResult(
        drift_breaches=drift_breaches,
        retrain_jobs=[job.id for job in retrain_jobs],
        targets_found=targets_found,
        tasks_spawned=tasks_spawned,
        alerts=alerts,
    )
    logger.info(
        "adaptation cycle done tenant_id=%s brand_id=%s drift_breaches=%d retrain_jobs=%d "
        "targets_found=%d tasks_spawned=%d",
        tenant_id,
        brand_id,
        result.drift_breaches,
        len(result.retrain_jobs),
        result.targets_found,
        result.tasks_spawned,
    )
    return result
