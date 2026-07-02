# M4-T17 — M4 validation (compliance gate enforced · loop closed · no live posting)

**Depends on:** T01–T16 (all M4 tasks merged) · **Wave:** 3
**Suggested agent:** general-purpose (final integration/validation task)

**Goal:** The M4 definition-of-done gate. An end-to-end, hermetic test that proves the closed loop
holds together **and** that the white-hat compliance gate (PRD NG1) is unbypassable across the seeding
subsystem. Asserts: (1) discovery → brief → compliance → workflow reaches `PLACED` only for a clean
proposal; (2) an astroturf/hidden-text proposal **cannot** be placed anywhere; (3) a drift breach
drives a retrain job; (4) a full adaptation cycle spawns tasks and produces alerts; (5) billing
composes usage + RaaS — **with zero live network, AWS, or posting**.

**Files:**
- Create: `tests/test_m4_e2e.py`
- Edit (docs): tick M4 status in `docs/tasks/M4-README.md` header note if desired (optional).

## Interface
No new production code — this is a cross-module validation test. It imports the real M4 modules and
wires them with in-repo fakes for the injected protocols (`SourceMap`, `BriefLLM`, `Retrainer`,
`AttributionSource`).

## Steps
- [ ] **1. Failing test** `tests/test_m4_e2e.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from gw_geo.common.db import Base, SeedingTask, DriftEvent
from gw_geo.common.models import SourceType
from gw_geo.seeding.channels import ChannelCatalog, seed_channels, seed_compliance_rules
from gw_geo.seeding.compliance import ComplianceEngine, PlacementProposal, ComplianceError
from gw_geo.seeding.discovery import discover_targets
from gw_geo.seeding.workflow import SeedingWorkflow, SeedingStatus
from gw_geo.orchestration.retrain import RetrainTrigger
from gw_geo.billing.metering import UsageKind, record_usage
from gw_geo.billing.pricing import PricingPlan, AttributedResults
from gw_geo.billing.views import billing_summary

class FakeSourceMap:
    def citation_source_mix(self, *, tenant_id, brand_id, since, until):
        return {"sources": [{"domain": "reddit.com", "source_type": "reddit",
                "engine": "perplexity", "you_pct": 0.1, "competitor_pct": 0.7}]}
class FakeRetrainer:
    def retrain(self, *, engine): return {"model_ref": "s3://m/v2", "metrics": {"auc": 0.8}}
class FakeAttribution:
    def attributed_results(self, *, tenant_id, brand_id, period_start, period_end):
        return AttributedResults(attributed_leads=50, attributed_pipeline_usd=0.0)

def _session():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); return Session(eng)

def test_discovery_to_placement_happy_path():
    s = _session(); seed_channels(s); seed_compliance_rules(s); s.commit()
    targets = discover_targets(FakeSourceMap(), tenant_id="t1", brand_id="b1",
        since="a", until="b", channels=ChannelCatalog.default())
    assert targets and targets[0].channel == "reddit"
    wf = SeedingWorkflow(s, tenant_id="t1", engine=ComplianceEngine(ComplianceEngine.default_ruleset()))
    tid = wf.create(brand_id="b1", channel="reddit", target_url=targets[0].domain)
    clean = PlacementProposal(channel="reddit", body="Honest CRM comparison.",
                              disclosure_text="Disclosure: I work at Acme.", author_is_real=True)
    assert wf.run_compliance(tid, clean).passed is True
    wf.mark_placed(tid, placed_url="https://reddit.com/r/x/1", actor="alice")
    assert s.get(SeedingTask, tid).status == SeedingStatus.PLACED

def test_white_hat_gate_blocks_astroturf_everywhere():
    s = _session(); seed_compliance_rules(s); s.commit()
    wf = SeedingWorkflow(s, tenant_id="t1", engine=ComplianceEngine(ComplianceEngine.default_ruleset()))
    for channel in ("reddit", "g2", "wikipedia", "quora"):
        tid = wf.create(brand_id="b1", channel=channel)
        bad = PlacementProposal(channel=channel, body="Acme is #1!!!",
                                disclosure_text="", author_is_real=False)   # astroturf + no disclosure
        assert wf.run_compliance(tid, bad).passed is False
        with pytest.raises(ComplianceError):
            wf.mark_placed(tid, placed_url="https://x", actor="bot")

def test_drift_breach_drives_retrain():
    s = _session()
    s.add(DriftEvent(id="d1", engine="perplexity", canary_id="c1", baseline_rate=0.6,
        observed_rate=0.3, drop=0.3, breached=True, retrain_flag=True)); s.commit()
    jobs = RetrainTrigger(s, retrainer=FakeRetrainer()).poll()
    assert len(jobs) == 1 and jobs[0].status == "succeeded"

def test_billing_composes_usage_and_raas():
    s = _session()
    record_usage(s, tenant_id="t1", brand_id="b1", kind=UsageKind.PROBE,
                 quantity=1000, ts="2026-06-10"); s.commit()
    plan = PricingPlan(plan="enterprise", base_fee=1000.0, usage_rates={"probe": 0.001},
                       raas_enabled=True, raas_basis="per_lead", raas_rate=10.0)
    out = billing_summary(s, tenant_id="t1", plan=plan, attribution=FakeAttribution(),
                          period_start="2026-06-01", period_end="2026-07-01")
    assert out["total"] == 1000.0 + 1.0 + 500.0
```

- [ ] **2. Run → fail** (until all deps merged).
- [ ] **3. Fix wiring only** (no interface changes) until green; if a real interface must change,
  surface it to the orchestrator (do not silently diverge from the design spec).
- [ ] **4. Run → pass**; whole suite green: `pytest -q`, `ruff check`, `mypy src/gw_geo/common`.
- [ ] **5. Commit:** `test(m4): end-to-end closed-loop + white-hat gate validation`

## Acceptance
- Full loop is green end-to-end with only in-repo fakes; the compliance gate **blocks astroturf on
  every channel and cannot be bypassed** (PRD NG1); drift breach → retrain; billing composes usage +
  RaaS; **no live network, AWS, or posting** anywhere in the suite. This is the M4 done-gate.
