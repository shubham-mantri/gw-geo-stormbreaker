"""Tests for the compliance-gated, human-in-the-loop seeding workflow (m4-design.md S2.5,
docs/tasks/M4-T10-seeding-workflow.md) -- the M4 enforcement point for PRD NG1 (with T03).

`docs/tasks/M4-T10-seeding-workflow.md` step 1 mandates the three tests below verbatim: a clean
proposal flows todo -> briefed -> ready_for_human -> placed; a blocked (block-severity) proposal
is rejected by `run_compliance` and `mark_placed` still refuses it with `ComplianceError`; and
`mark_placed` refuses a task that never had compliance run at all (still `ready_for_human`'s
precondition is unmet) -- in both failure cases the gate holds even though the caller invoked
`mark_placed` directly, with no way to bypass it. `test_illegal_transition_raises` closes out the
task's acceptance criteria: a transition attempted from the wrong status (`attach_brief` on an
already-`PLACED` task) must raise rather than silently mutating the row.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.db import Base, Brand, Tenant, UsageEvent
from gw_geo.seeding.briefs import SeedingBrief
from gw_geo.seeding.compliance import ComplianceEngine, ComplianceError, PlacementProposal
from gw_geo.seeding.workflow import (  # SeedingTask re-exported or from db
    IllegalTransitionError,
    SeedingStatus,
    SeedingTask,
    SeedingWorkflow,
)


def _wf():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    # Seed the FK parents every workflow task references (SeedingTask.tenant_id -> tenant.id,
    # .brand_id -> brand.id) before wf.create() inserts tasks under FK enforcement.
    s.add(Tenant(id="t1", name="t", sampling_budget_daily=100.0))
    s.add(Brand(id="b1", tenant_id="t1", name="b", domain="b.com"))
    s.commit()
    return SeedingWorkflow(s, tenant_id="t1", engine=ComplianceEngine(ComplianceEngine.default_ruleset())), s


def test_clean_proposal_flows_to_placed():
    wf, s = _wf()
    tid = wf.create(brand_id="b1", channel="reddit")
    good = PlacementProposal(channel="reddit", body="Genuine comparison of CRMs.",
                             disclosure_text="Disclosure: I work at Acme.", author_is_real=True)
    rep = wf.run_compliance(tid, good)
    assert rep.passed is True
    wf.mark_placed(tid, placed_url="https://reddit.com/r/x/comments/1", actor="alice")
    assert s.get(SeedingTask, tid).status == SeedingStatus.PLACED


def test_mark_placed_records_seeding_placement_usage():
    # Billing metering hook: a successful placement records one SEEDING_PLACEMENT usage unit
    # (metering only -- the human-only, compliance-gated transition itself is unchanged).
    wf, s = _wf()
    tid = wf.create(brand_id="b1", channel="reddit")
    good = PlacementProposal(channel="reddit", body="Genuine comparison of CRMs.",
                             disclosure_text="Disclosure: I work at Acme.", author_is_real=True)
    wf.run_compliance(tid, good)
    wf.mark_placed(tid, placed_url="https://reddit.com/r/x/comments/1", actor="alice")

    events = s.query(UsageEvent).all()
    assert len(events) == 1
    assert events[0].kind == "seeding_placement"
    assert events[0].quantity == 1
    assert events[0].source_ref == tid
    assert events[0].tenant_id == "t1" and events[0].brand_id == "b1"


def test_blocked_placement_records_no_usage():
    # A gate-blocked placement is refused before the metering line -> no usage recorded.
    wf, s = _wf()
    tid = wf.create(brand_id="b1", channel="reddit")
    bad = PlacementProposal(channel="reddit", body="Acme rules", disclosure_text="",
                            author_is_real=False)
    wf.run_compliance(tid, bad)
    with pytest.raises(ComplianceError):
        wf.mark_placed(tid, placed_url="https://reddit.com/x", actor="alice")
    assert s.query(UsageEvent).count() == 0


def test_blocked_proposal_cannot_be_placed():
    wf, s = _wf()
    tid = wf.create(brand_id="b1", channel="reddit")
    bad = PlacementProposal(channel="reddit", body="Acme rules", disclosure_text="",
                            author_is_real=False)                    # astroturf
    rep = wf.run_compliance(tid, bad)
    assert rep.passed is False
    with pytest.raises(ComplianceError):
        wf.mark_placed(tid, placed_url="https://reddit.com/x", actor="alice")   # gate holds


def test_mark_placed_requires_prior_compliance():
    wf, s = _wf()
    tid = wf.create(brand_id="b1", channel="g2")                    # never ran compliance
    with pytest.raises(ComplianceError):
        wf.mark_placed(tid, placed_url="https://g2.com/x", actor="bob")


def test_illegal_transition_raises():
    wf, s = _wf()
    tid = wf.create(brand_id="b1", channel="reddit")
    good = PlacementProposal(channel="reddit", body="Genuine comparison of CRMs.",
                             disclosure_text="Disclosure: I work at Acme.", author_is_real=True)
    wf.run_compliance(tid, good)
    wf.mark_placed(tid, placed_url="https://reddit.com/r/x/comments/1", actor="alice")
    brief = SeedingBrief(channel="reddit", talking_points=[], grounded_facts=[],
                         disclosure_text="Disclosure: I work at Acme.", format_notes="n/a")
    with pytest.raises(IllegalTransitionError):
        wf.attach_brief(tid, brief)  # already PLACED -- attach_brief is now illegal


def test_run_compliance_rejects_channel_substitution_attack():
    """Channel-substitution bypass (PRD NG1): a task created on `wikipedia` but reviewed with a
    `pr_wire` proposal (the one channel needing no disclosure) would dodge wikipedia's
    `disclosure_required` + `wikipedia_no_paid_self_edit` rules and reach a green report. The gate
    must refuse to evaluate a proposal whose channel differs from the task's -- and the task must
    never reach PLACED.
    """
    wf, s = _wf()
    tid = wf.create(brand_id="b1", channel="wikipedia")
    sneaky = PlacementProposal(channel="pr_wire", body="Acme is the leading vendor.",
                               disclosure_text="", is_paid=True, author_is_real=True)
    with pytest.raises(ComplianceError):
        wf.run_compliance(tid, sneaky)
    assert s.get(SeedingTask, tid).status == SeedingStatus.TODO  # never advanced
    with pytest.raises(ComplianceError):
        wf.mark_placed(tid, placed_url="https://en.wikipedia.org/wiki/Acme", actor="bot")


def test_paid_undisclosed_wikipedia_is_blocked_not_placed():
    """The honest form of the same scenario: submitted on its real channel, a paid + undisclosed
    wikipedia placement is block-severity, gets `rejected`, and `mark_placed` refuses it.
    """
    wf, s = _wf()
    tid = wf.create(brand_id="b1", channel="wikipedia")
    proposal = PlacementProposal(channel="wikipedia", body="Acme is the leading vendor.",
                                 disclosure_text="", is_paid=True, author_is_real=True)
    rep = wf.run_compliance(tid, proposal)
    assert rep.passed is False
    assert s.get(SeedingTask, tid).status == SeedingStatus.REJECTED
    with pytest.raises(ComplianceError):
        wf.mark_placed(tid, placed_url="https://en.wikipedia.org/wiki/Acme", actor="bot")


def test_mark_placed_rejects_stored_report_channel_mismatch():
    """Defense-in-depth: even if a passing report for a *different* channel were somehow persisted
    with status `ready_for_human`, `mark_placed` must refuse it (the stored report's channel must
    match the task's channel).
    """
    wf, s = _wf()
    tid = wf.create(brand_id="b1", channel="wikipedia")
    task = s.get(SeedingTask, tid)
    task.status = SeedingStatus.READY_FOR_HUMAN.value
    task.compliance_status = "passed"
    task.compliance_report = {"channel": "pr_wire", "passed": True, "violations": []}
    s.commit()
    with pytest.raises(ComplianceError):
        wf.mark_placed(tid, placed_url="https://en.wikipedia.org/wiki/Acme", actor="bot")
