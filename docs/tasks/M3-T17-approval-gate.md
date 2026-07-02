# M3-T17 — Human approval gate

**Depends on:** T03, T02 · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** The enterprise **human approval gate** (PRD §6.4, ui-spec §3.5 "approval gates are
explicit"). A state machine `DRAFT → PENDING_REVIEW → APPROVED → PUBLISHED` (or `REJECTED`).
`approve()` requires **(a)** `GuardrailReport.passed is True` **and (b)** a role in
`{editor, admin, owner}` (ui-spec §5 RBAC) — else `ApprovalError`. `ensure_publishable()` raises unless
status is `APPROVED`. **Nothing bypasses this gate** — the invariant is asserted directly.

**Files:**
- Create: `src/gw_geo/content/approval.py`
- Test: `tests/content/test_approval.py`

## Interface

```python
from gw_geo.common.models import ContentDraft, ContentStatus, GuardrailReport

class ApprovalError(Exception): ...

APPROVER_ROLES = frozenset({"editor", "admin", "owner"})

def submit_for_review(draft: ContentDraft) -> ContentDraft: ...    # DRAFT -> PENDING_REVIEW
def approve(draft: ContentDraft, *, report: GuardrailReport, role: str) -> ContentDraft: ...
def reject(draft: ContentDraft, *, role: str) -> ContentDraft: ...
def ensure_publishable(draft: ContentDraft) -> None: ...           # raises unless APPROVED
```

## Steps
- [ ] **1. Failing test** `tests/content/test_approval.py`:

```python
import pytest
from gw_geo.common.models import ContentDraft, ContentStatus, GuardrailReport
from gw_geo.content.approval import (submit_for_review, approve, ensure_publishable,
                                    ApprovalError)

def _draft(status=ContentStatus.DRAFT):
    return ContentDraft(id="c1", tenant_id="t1", brand_id="b1", title="T",
                        body_markdown="x", status=status)

def _report(passed=True):
    return GuardrailReport(originality_ok=passed, originality_score=0.1, claims_ok=passed,
                           unverified_claims=[] if passed else ["x"], brand_voice_ok=passed,
                           brand_voice_score=0.9, passed=passed)

def test_happy_path():
    d = submit_for_review(_draft())
    assert d.status == ContentStatus.PENDING_REVIEW
    d = approve(d, report=_report(True), role="editor")
    assert d.status == ContentStatus.APPROVED
    ensure_publishable(d)   # does not raise

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
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `approval.py`. `approve` raises `ApprovalError` unless `report.passed` and
  `role in APPROVER_ROLES`; returns a copy with `status=APPROVED`. `ensure_publishable` raises unless
  `APPROVED`.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(content): human approval gate (guardrail + RBAC preconditions)`

## Acceptance
- Approval requires a passing `GuardrailReport` AND an authorized role; publish is blocked unless
  `APPROVED`; every bypass path raises `ApprovalError` (directly tested).
