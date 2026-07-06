"""Compliance-gated, human-in-the-loop seeding placement workflow (`docs/m4-design.md` S2.5) --
the M4 enforcement point for PRD NG1 (with `seeding.compliance`, T03).

`SeedingWorkflow` is the state machine over `seeding_task` (T02):

    todo -> briefed -> compliance_review -> ready_for_human -> placed -> corroborated
                              |  (block-severity) `-> rejected

Every transition but the last two is a straightforward status advance. The last two are the
whole point of this module: **there is no auto-poster.** `run_compliance` is the only path that
can move a task to `ready_for_human`, and it does so by actually calling `ComplianceEngine.evaluate`
and persisting the resulting `ComplianceReport` -- never by trusting a caller-supplied verdict. It
also **binds the proposal to the task's channel**: because `evaluate` selects the applicable
per-channel rules from `proposal.channel`, `run_compliance` refuses (with `ComplianceError`) any
proposal whose channel differs from the task's, so a caller cannot create a `wikipedia` task and
review it under a `pr_wire` proposal to dodge that channel's stricter rules (PRD NG1).
`mark_placed` then **re-reads that persisted report from the database row** (not any report a
caller might have lying around) and hard-refuses with `ComplianceError` unless the row's `status`
is `ready_for_human`, the stored `report["passed"]` is `True`, **and** the stored report's
`channel` matches the task's channel (defense-in-depth for the same substitution attack). That
re-read is what makes
the gate unbypassable: `mark_placed` takes no `ComplianceReport`/`PlacementProposal` argument at
all, so there is no parameter a caller could ever pass to talk their way past the check --
including the "never ran compliance" case (`compliance_report` still its `{}` default), which
fails the same `is True` check as an explicit block-severity failure. `placed_url` is always
human-supplied; this module makes no network call and posts nothing itself (`docs/trd.md` S12).

Every other illegal transition (calling a step out of order, e.g. `attach_brief` on an already
`placed` task) raises `IllegalTransitionError` -- a distinct exception from `ComplianceError` so a
caller can tell "you called this out of order" apart from "the white-hat gate refused this
placement" (the latter is the one PRD-NG1-relevant failure mode, per `compliance.py`'s
`ComplianceError` docstring).

All reads/writes are tenant-scoped via `TenantScopedSession` (TRD S7): a task id belonging to
another tenant is indistinguishable from an unknown one, both raising `LookupError` (mirrors
`content.pipeline.ContentService.get_asset`) -- no cross-tenant existence leak.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from sqlalchemy.orm import Session as SASession

from gw_geo.billing.metering import UsageKind, record_usage
from gw_geo.common.db import SeedingTask, TenantScopedSession
from gw_geo.seeding.briefs import SeedingBrief
from gw_geo.seeding.compliance import (
    ComplianceEngine,
    ComplianceError,
    ComplianceReport,
    PlacementProposal,
)


class SeedingStatus(StrEnum):
    """`seeding_task.status` values (design S2.5)."""

    TODO = "todo"
    BRIEFED = "briefed"
    COMPLIANCE_REVIEW = "compliance_review"
    READY_FOR_HUMAN = "ready_for_human"
    PLACED = "placed"
    CORROBORATED = "corroborated"
    REJECTED = "rejected"


class IllegalTransitionError(Exception):
    """Raised when a workflow method is called against a `SeedingTask` in the wrong status.

    Distinct from `ComplianceError`: this signals a caller invoked a step out of order (e.g.
    `attach_brief` on an already-`placed` task), not that the white-hat compliance gate refused a
    placement -- `mark_placed`'s gate failures are always `ComplianceError`, never this.
    """


_PENDING = "pending"
_PASSED = "passed"
_FAILED = "failed"

# `run_compliance` is legal from either of these -- a brief is preparatory material for the human
# placer, not a precondition of the compliance gate itself (see `run_compliance`'s docstring).
_COMPLIANCE_ELIGIBLE_STATUSES = (SeedingStatus.TODO.value, SeedingStatus.BRIEFED.value)


class SeedingWorkflow:
    """Drives one tenant's `seeding_task` rows through the human-in-the-loop placement flow.

    `session` is a plain `Session` (e.g. `Session(engine)`); it is wrapped in a
    `TenantScopedSession` internally so every read/write this class performs is confined to
    `tenant_id` -- callers never hand in an already-scoped session. `engine` is the injected,
    pure `ComplianceEngine` (T03) `run_compliance` evaluates every proposal against; no network
    I/O happens anywhere in this class.
    """

    def __init__(self, session: SASession, tenant_id: str, engine: ComplianceEngine) -> None:
        self._scoped = TenantScopedSession(session, tenant_id)
        # Kept alongside the scoped wrapper only for the billing metering write in `mark_placed`
        # (`record_usage` takes a plain `Session`); every gate read/write still goes through
        # `self._scoped`.
        self._session = session
        self._tenant_id = tenant_id
        self._engine = engine

    def create(
        self,
        *,
        brand_id: str,
        channel: str,
        target_url: str | None = None,
        content_asset_id: str | None = None,
    ) -> str:
        """Create a new `seeding_task` row in `todo`, scoped to this workflow's tenant.

        `compliance_status` starts at `"pending"` and `compliance_report` at its column default
        (`{}`) -- both are exactly the "compliance has never run" state `mark_placed` refuses to
        place. Returns the new task's id.
        """
        task = SeedingTask(
            id=uuid4().hex,
            tenant_id=self._tenant_id,
            brand_id=brand_id,
            content_asset_id=content_asset_id,
            channel=channel,
            target_url=target_url,
            status=SeedingStatus.TODO.value,
            compliance_status=_PENDING,
        )
        self._scoped.add(task)
        self._scoped.commit()
        return task.id

    def attach_brief(self, task_id: str, brief: SeedingBrief) -> None:
        """Attach a `SeedingBrief` to `task_id`, moving it `todo -> briefed`.

        The full brief is persisted (JSON-serialized) into `brief_ref` so a subsequent
        `run_compliance`/human reviewer can recover exactly what the human placer was given.

        Raises:
            LookupError: no `task_id` exists for this tenant.
            IllegalTransitionError: the task's status is not `todo`.
        """
        task = self._get_task(task_id)
        if task.status != SeedingStatus.TODO.value:
            raise IllegalTransitionError(
                f"cannot attach brief to task {task_id!r} from status {task.status!r} "
                f"(must be {SeedingStatus.TODO.value!r})"
            )
        task.brief_ref = brief.model_dump_json()
        task.status = SeedingStatus.BRIEFED.value
        self._scoped.commit()

    def run_compliance(self, task_id: str, proposal: PlacementProposal) -> ComplianceReport:
        """Run `proposal` through the injected `ComplianceEngine` and persist the verdict.

        Moves `todo/briefed -> compliance_review -> ready_for_human` (if `report.passed`) or
        `-> rejected` (if any block-severity violation fired). A brief is *preparatory* material
        for the human placer, not a precondition of the compliance gate itself, so this accepts a
        task straight out of `create` (`todo`) as well as a `briefed` one -- the spec's own
        `test_clean_proposal_flows_to_placed`/`test_mark_placed_requires_prior_compliance` never
        call `attach_brief` at all. The `ComplianceReport` is always written to
        `seeding_task.compliance_report` first, so `mark_placed` always has a persisted,
        re-readable verdict to check regardless of the outcome here.

        The proposal's `channel` **must** equal the task's `channel`: `ComplianceEngine.evaluate`
        selects which per-channel rules apply from `proposal.channel`, so evaluating a task's
        placement against a *different* channel's proposal would silently apply the wrong (weaker)
        ruleset -- e.g. submitting a `pr_wire` proposal (the one channel needing no disclosure)
        for a `wikipedia` task dodges `disclosure_required` + `wikipedia_no_paid_self_edit`. A
        mismatch is refused with `ComplianceError` rather than silently coerced: the caller must
        submit a proposal for the task's real channel (PRD NG1).

        Raises:
            LookupError: no `task_id` exists for this tenant.
            IllegalTransitionError: the task's status is not `todo` or `briefed` (i.e. compliance
                has already been decided for this task, or it has moved past that point).
            ComplianceError: `proposal.channel` differs from the task's channel (a
                channel-substitution attempt), or propagated from `ComplianceEngine.evaluate` if a
                rule references an unresolvable check key (a misconfigured ruleset must fail
                loudly, per `compliance.py`) -- in both cases the task is left at its current
                status, not silently advanced.
        """
        task = self._get_task(task_id)
        if task.status not in _COMPLIANCE_ELIGIBLE_STATUSES:
            raise IllegalTransitionError(
                f"cannot run compliance on task {task_id!r} from status {task.status!r} "
                f"(must be one of {list(_COMPLIANCE_ELIGIBLE_STATUSES)!r})"
            )
        if proposal.channel != task.channel:
            raise ComplianceError(
                f"proposal.channel {proposal.channel!r} does not match task {task_id!r}'s channel "
                f"{task.channel!r}: a placement must be evaluated against its task's real channel "
                "(submitting a different channel's proposal would evade that channel's compliance "
                "rules)."
            )
        report = self._engine.evaluate(proposal)
        task.compliance_report = report.model_dump()
        task.compliance_status = _PASSED if report.passed else _FAILED
        task.status = (
            SeedingStatus.READY_FOR_HUMAN.value if report.passed else SeedingStatus.REJECTED.value
        )
        self._scoped.commit()
        return report

    def mark_placed(self, task_id: str, *, placed_url: str, actor: str) -> None:
        """Mark `task_id` `placed` -- the sole human-actioned, compliance-gated transition.

        Re-reads the task's *persisted* `status` and `compliance_report` (never a value the
        caller might supply) and raises `ComplianceError` unless **all** of: the status is
        `ready_for_human`, the stored report's `passed` is `True`, **and** the stored report's
        `channel` matches the task's `channel`. This is the hard gate: there is no argument to
        this method that lets a caller supply or override a verdict, so a blocked task, a task
        still awaiting compliance review, and a task where compliance was never run at all
        (`compliance_report` still `{}`) are all refused identically. The stored-channel check is
        defense-in-depth against a channel-substitution report ever reaching this row (the primary
        guard is in `run_compliance`; `run_compliance` persists the report's `channel`, which
        equals the proposal's -- itself now forced to equal the task's). No network call is made
        -- `placed_url` is human-supplied.

        Raises:
            LookupError: no `task_id` exists for this tenant.
            ComplianceError: `status != ready_for_human`, the stored report did not pass, or the
                stored report's channel does not match the task's channel.
        """
        task = self._get_task(task_id)
        stored_report = task.compliance_report or {}
        stored_passed = stored_report.get("passed")
        stored_channel = stored_report.get("channel")
        if (
            task.status != SeedingStatus.READY_FOR_HUMAN.value
            or stored_passed is not True
            or stored_channel != task.channel
        ):
            raise ComplianceError(
                f"cannot mark task {task_id!r} placed: status={task.status!r}, "
                f"stored compliance_report.passed={stored_passed!r}, "
                f"stored compliance_report.channel={stored_channel!r} (task channel "
                f"{task.channel!r}) -- requires status={SeedingStatus.READY_FOR_HUMAN.value!r}, a "
                "persisted passing compliance report, and a matching report channel"
            )
        task.status = SeedingStatus.PLACED.value
        task.placed_url = placed_url
        task.actor = actor
        # Billing metering (m4-design §4.1): a successful, human-actioned placement is one billable
        # SEEDING_PLACEMENT unit. Metering only -- this line records usage and changes nothing about
        # the white-hat gate above; it is staged before (and flushed by) the existing commit.
        record_usage(
            self._session,
            tenant_id=task.tenant_id,
            brand_id=task.brand_id,
            kind=UsageKind.SEEDING_PLACEMENT,
            quantity=1,
            ts=datetime.now(timezone.utc).isoformat(),
            source_ref=task_id,
        )
        self._scoped.commit()

    def _get_task(self, task_id: str) -> SeedingTask:
        """Look up `task_id`, scoped to this workflow's tenant.

        Raises:
            LookupError: `task_id` doesn't exist, or belongs to a different tenant -- the two
                cases deliberately collapse to the same error (no cross-tenant existence leak,
                mirrors `content.pipeline.ContentService.get_asset`).
        """
        task = self._scoped.query(SeedingTask).filter(SeedingTask.id == task_id).one_or_none()
        if task is None:
            raise LookupError(f"seeding task {task_id!r} not found")
        return task


__all__ = [
    "IllegalTransitionError",
    "SeedingStatus",
    "SeedingTask",
    "SeedingWorkflow",
]
