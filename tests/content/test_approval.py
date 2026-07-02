"""Tests for the human approval gate (`gw_geo.content.approval`) -- the Athena fix precondition:

nothing publishes without BOTH a passing `GuardrailReport` and an authorized human reviewer
(PRD §6.4/§13, ui-spec §3.5/§5). Every bypass path below is asserted to raise `ApprovalError`.
"""

import pytest

from gw_geo.common.models import ContentDraft, ContentStatus, GuardrailReport
from gw_geo.content.approval import (
    ApprovalError,
    approve,
    ensure_publishable,
    reject,
    submit_for_review,
)


def _draft(status: ContentStatus = ContentStatus.DRAFT) -> ContentDraft:
    return ContentDraft(
        id="c1",
        tenant_id="t1",
        brand_id="b1",
        title="T",
        body_markdown="x",
        status=status,
    )


def _report(passed: bool = True) -> GuardrailReport:
    return GuardrailReport(
        originality_ok=passed,
        originality_score=0.1,
        claims_ok=passed,
        unverified_claims=[] if passed else ["x"],
        brand_voice_ok=passed,
        brand_voice_score=0.9,
        passed=passed,
    )


def test_happy_path():
    d = submit_for_review(_draft())
    assert d.status == ContentStatus.PENDING_REVIEW
    d = approve(d, report=_report(True), role="editor")
    assert d.status == ContentStatus.APPROVED
    ensure_publishable(d)  # does not raise


def test_guardrail_failure_blocks_approval():
    d = submit_for_review(_draft())
    with pytest.raises(ApprovalError):
        approve(d, report=_report(passed=False), role="editor")


def test_viewer_cannot_approve():
    d = submit_for_review(_draft())
    with pytest.raises(ApprovalError):
        approve(d, report=_report(True), role="viewer")


def test_publish_blocked_without_approval():
    with pytest.raises(ApprovalError):
        ensure_publishable(_draft(ContentStatus.DRAFT))
    with pytest.raises(ApprovalError):
        ensure_publishable(_draft(ContentStatus.PENDING_REVIEW))


# --- Additional coverage beyond the spec-mandated tests above ---------------------------------


def test_guardrail_failure_and_bad_role_both_block_independently():
    # Neither condition alone is sufficient without the other -- a failing report is not
    # rescued by an authorized role.
    d = submit_for_review(_draft())
    with pytest.raises(ApprovalError):
        approve(d, report=_report(passed=False), role="owner")


def test_admin_and_owner_can_also_approve():
    for role in ("admin", "owner"):
        d = submit_for_review(_draft())
        d = approve(d, report=_report(True), role=role)
        assert d.status == ContentStatus.APPROVED


def test_approve_requires_pending_review_status():
    with pytest.raises(ApprovalError):
        approve(_draft(ContentStatus.DRAFT), report=_report(True), role="editor")


def test_submit_for_review_requires_draft_status():
    with pytest.raises(ApprovalError):
        submit_for_review(_draft(ContentStatus.APPROVED))


def test_reject_moves_pending_review_to_rejected():
    d = submit_for_review(_draft())
    d = reject(d, role="editor")
    assert d.status == ContentStatus.REJECTED
    with pytest.raises(ApprovalError):
        ensure_publishable(d)


def test_reject_requires_approver_role():
    d = submit_for_review(_draft())
    with pytest.raises(ApprovalError):
        reject(d, role="viewer")


def test_state_transitions_return_new_instances_without_mutating_input():
    original = _draft()
    submitted = submit_for_review(original)
    assert original.status == ContentStatus.DRAFT
    assert submitted.status == ContentStatus.PENDING_REVIEW
    assert submitted is not original
