"""Human approval gate (PRD §6.4/§13, ui-spec §3.5/§5) -- the Athena fix.

Athena's documented failure was publishing content with no human in the loop. This module is
the enterprise **human approval gate** that makes that structurally impossible here: a small
state machine, `DRAFT -> PENDING_REVIEW -> APPROVED -> PUBLISHED` (or `REJECTED`), where
`approve()` is the single chokepoint every draft must pass through before publish is even
possible.

`approve()` raises `ApprovalError` unless **both** hold:
  (a) the draft's `GuardrailReport.passed` is `True` (originality + claim-verification +
      brand-voice all clean, PRD §6.4), and
  (b) `role` is an authorized reviewer role (`APPROVER_ROLES`, ui-spec §5 RBAC: `owner`,
      `admin`, `editor` can approve/publish; `viewer` cannot).

`ensure_publishable()` is the final, unconditional check the publish pipeline
(`gw_geo.content.publish`) must call before ever invoking a `PublishConnector`: it raises unless
the draft's status is `APPROVED`. Every path that could bypass either check raises
`ApprovalError` rather than failing silently or returning a sentinel -- there is no "publish
anyway" escape hatch.

All transitions return a *new* `ContentDraft` (via pydantic's `model_copy`) rather than mutating
the input in place, so callers can never accidentally observe a half-transitioned draft.
"""

from __future__ import annotations

from gw_geo.common.models import ContentDraft, ContentStatus, GuardrailReport


class ApprovalError(Exception):
    """Raised whenever a status transition or publish precondition is violated.

    This is the *only* signal the gate ever gives for a disallowed transition -- there is no
    silent no-op path, so callers cannot accidentally treat a blocked transition as success.
    """


APPROVER_ROLES = frozenset({"editor", "admin", "owner"})
"""Roles authorized to approve/reject content for publish (ui-spec §5 RBAC). `viewer` is not
a member and therefore can never approve or reject, only `ensure_publishable`'s status check
gates publish itself.
"""


def submit_for_review(draft: ContentDraft) -> ContentDraft:
    """Move `draft` from `DRAFT` to `PENDING_REVIEW`.

    Raises:
        ApprovalError: `draft.status` is not `DRAFT`.
    """
    if draft.status != ContentStatus.DRAFT:
        raise ApprovalError(
            f"cannot submit for review from status {draft.status!r} (must be DRAFT)"
        )
    return draft.model_copy(update={"status": ContentStatus.PENDING_REVIEW})


def approve(draft: ContentDraft, *, report: GuardrailReport, role: str) -> ContentDraft:
    """Move `draft` from `PENDING_REVIEW` to `APPROVED`.

    This is the human approval gate itself: approval requires **both** a passing guardrail
    report and an authorized reviewer role. Neither condition alone is sufficient -- a clean
    report from an unauthorized role, or an authorized role rubber-stamping a failing report,
    are both refused.

    Raises:
        ApprovalError: `draft.status` is not `PENDING_REVIEW`, or `report.passed` is not
            `True`, or `role` is not a member of `APPROVER_ROLES`.
    """
    if draft.status != ContentStatus.PENDING_REVIEW:
        raise ApprovalError(
            f"cannot approve from status {draft.status!r} (must be PENDING_REVIEW)"
        )
    if not report.passed:
        raise ApprovalError("cannot approve: guardrail report did not pass")
    if role not in APPROVER_ROLES:
        raise ApprovalError(f"cannot approve: role {role!r} is not an approver role")
    return draft.model_copy(update={"status": ContentStatus.APPROVED})


def reject(draft: ContentDraft, *, role: str) -> ContentDraft:
    """Move `draft` from `PENDING_REVIEW` to `REJECTED`.

    Rejection is gated by the same RBAC as approval (ui-spec §5) -- both are reviewer
    decisions on a pending draft.

    Raises:
        ApprovalError: `draft.status` is not `PENDING_REVIEW`, or `role` is not a member of
            `APPROVER_ROLES`.
    """
    if draft.status != ContentStatus.PENDING_REVIEW:
        raise ApprovalError(
            f"cannot reject from status {draft.status!r} (must be PENDING_REVIEW)"
        )
    if role not in APPROVER_ROLES:
        raise ApprovalError(f"cannot reject: role {role!r} is not an approver role")
    return draft.model_copy(update={"status": ContentStatus.REJECTED})


def ensure_publishable(draft: ContentDraft) -> None:
    """Raise unless `draft.status` is `APPROVED`.

    The publish pipeline must call this immediately before invoking a `PublishConnector`
    (`gw_geo.content.publish.base`); it is the last-line, unconditional enforcement of "nothing
    publishes without explicit approval" (ui-spec §3.5) -- independent of how (or whether) the
    caller navigated `submit_for_review`/`approve` to get here.

    Raises:
        ApprovalError: `draft.status` is not `APPROVED`.
    """
    if draft.status != ContentStatus.APPROVED:
        raise ApprovalError(f"cannot publish from status {draft.status!r} (must be APPROVED)")
