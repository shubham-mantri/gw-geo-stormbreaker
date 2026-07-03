"""M4 end-to-end validation (docs/tasks/M4-T17-m4-validation.md) -- the M4 done-gate.

Four cross-module, hermetic tests proving the closed loop holds together and that the white-hat
compliance gate (PRD NG1) is unbypassable, wiring the *real* M4 modules with the same in-repo
fakes their own unit suites already use for the injected protocols (`SourceMap`, `Retrainer`,
`AttributionSource`):

1. `test_discovery_to_placement_happy_path` -- discovery -> compliance -> workflow reaches
   `PLACED` for a clean, disclosed proposal.
2. `test_white_hat_gate_blocks_astroturf_everywhere` -- an astroturf/no-disclosure proposal is
   blocked by `run_compliance` on *every* channel, and `mark_placed` still refuses with
   `ComplianceError` -- the gate cannot be talked past.
3. `test_drift_breach_drives_retrain` -- a breached+flagged `DriftEvent` drives exactly one
   succeeded retrain job.
4. `test_billing_composes_usage_and_raas` -- `billing_summary` composes metered usage with a
   RaaS charge.

Every session is in-memory SQLite and every external collaborator is an in-repo fake: no live
network, AWS, or posting anywhere in this module.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.billing.metering import UsageKind, record_usage
from gw_geo.billing.pricing import AttributedResults, PricingPlan
from gw_geo.billing.views import billing_summary
from gw_geo.common.db import Base, DriftEvent, SeedingTask
from gw_geo.orchestration.retrain import RetrainTrigger
from gw_geo.seeding.channels import ChannelCatalog, seed_channels, seed_compliance_rules
from gw_geo.seeding.compliance import ComplianceEngine, ComplianceError, PlacementProposal
from gw_geo.seeding.discovery import discover_targets
from gw_geo.seeding.workflow import SeedingStatus, SeedingWorkflow


class FakeSourceMap:
    def citation_source_mix(self, *, tenant_id, brand_id, since, until):
        return {"sources": [{"domain": "reddit.com", "source_type": "reddit",
                "engine": "perplexity", "you_pct": 0.1, "competitor_pct": 0.7}]}


class FakeRetrainer:
    def retrain(self, *, engine):
        return {"model_ref": "s3://m/v2", "metrics": {"auc": 0.8}}


class FakeAttribution:
    def attributed_results(self, *, tenant_id, brand_id, period_start, period_end):
        return AttributedResults(attributed_leads=50, attributed_pipeline_usd=0.0)


def _session() -> Session:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return Session(eng)


def test_discovery_to_placement_happy_path():
    s = _session()
    seed_channels(s)
    seed_compliance_rules(s)
    s.commit()

    targets = discover_targets(
        FakeSourceMap(), tenant_id="t1", brand_id="b1",
        since="a", until="b", channels=ChannelCatalog.default(),
    )
    assert targets and targets[0].channel == "reddit"

    wf = SeedingWorkflow(
        s, tenant_id="t1", engine=ComplianceEngine(ComplianceEngine.default_ruleset())
    )
    tid = wf.create(brand_id="b1", channel="reddit", target_url=targets[0].domain)
    clean = PlacementProposal(
        channel="reddit", body="Honest CRM comparison.",
        disclosure_text="Disclosure: I work at Acme.", author_is_real=True,
    )
    assert wf.run_compliance(tid, clean).passed is True

    wf.mark_placed(tid, placed_url="https://reddit.com/r/x/1", actor="alice")
    assert s.get(SeedingTask, tid).status == SeedingStatus.PLACED


def test_white_hat_gate_blocks_astroturf_everywhere():
    s = _session()
    seed_compliance_rules(s)
    s.commit()

    wf = SeedingWorkflow(
        s, tenant_id="t1", engine=ComplianceEngine(ComplianceEngine.default_ruleset())
    )
    for channel in ("reddit", "g2", "wikipedia", "quora"):
        tid = wf.create(brand_id="b1", channel=channel)
        bad = PlacementProposal(  # astroturf + no disclosure
            channel=channel, body="Acme is #1!!!",
            disclosure_text="", author_is_real=False,
        )
        assert wf.run_compliance(tid, bad).passed is False
        with pytest.raises(ComplianceError):
            wf.mark_placed(tid, placed_url="https://x", actor="bot")


def test_drift_breach_drives_retrain():
    s = _session()
    s.add(DriftEvent(
        id="d1", engine="perplexity", canary_id="c1", baseline_rate=0.6,
        observed_rate=0.3, drop=0.3, breached=True, retrain_flag=True,
        ts=datetime.now(timezone.utc),
    ))
    s.commit()

    jobs = RetrainTrigger(s, retrainer=FakeRetrainer()).poll()
    assert len(jobs) == 1 and jobs[0].status == "succeeded"


def test_billing_composes_usage_and_raas():
    s = _session()
    record_usage(
        s, tenant_id="t1", brand_id="b1", kind=UsageKind.PROBE,
        quantity=1000, ts="2026-06-10",
    )
    s.commit()

    plan = PricingPlan(
        plan="enterprise", base_fee=1000.0, usage_rates={"probe": 0.001},
        raas_enabled=True, raas_basis="per_lead", raas_rate=10.0,
    )
    out = billing_summary(
        s, tenant_id="t1", plan=plan, attribution=FakeAttribution(),
        period_start="2026-06-01", period_end="2026-07-01",
    )
    assert out["total"] == 1000.0 + 1.0 + 500.0
