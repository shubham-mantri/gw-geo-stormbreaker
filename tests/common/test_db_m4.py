"""SQLite roundtrip tests for the M4 schema (m4-design §2.7/§3/§4.4): seeding, self-adaptation,
and billing tables. Mirrors the M0/M1/M2/M3 `test_db*.py` style -- in-memory SQLite,
`Base.metadata.create_all`.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from gw_geo.common.db import (
    Base,
    BillingAccount,
    BillingInvoice,
    Brand,
    ComplianceRule,
    DriftEvent,
    EffortBanditArm,
    RetrainJob,
    SeedingChannel,
    SeedingTask,
    Tenant,
    UsageEvent,
)


def _session() -> Session:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    # The tenant-scoped roundtrips below seed rows under tenant t1 / brand b1; seed those FK
    # parents once (the system-level tables ignore them).
    s.add(Tenant(id="t1", name="t", sampling_budget_daily=100.0))
    s.add(Brand(id="b1", tenant_id="t1", name="b", domain="b.com"))
    s.commit()
    return s


def test_seeding_task_persists_report_json() -> None:
    s = _session()
    s.add(
        SeedingTask(
            id="st1",
            tenant_id="t1",
            brand_id="b1",
            channel="reddit",
            status="compliance_review",
            compliance_status="pending",
            compliance_report={"passed": False, "violations": []},
            corroboration_count=0,
        )
    )
    s.commit()
    row = s.scalar(select(SeedingTask).where(SeedingTask.id == "st1"))
    assert row is not None
    assert row.compliance_report["passed"] is False
    assert row.channel == "reddit"


def test_seeding_channel_and_compliance_rule_are_system_level() -> None:
    assert "tenant_id" not in SeedingChannel.__table__.columns
    assert "tenant_id" not in ComplianceRule.__table__.columns
    s = _session()
    s.add(
        SeedingChannel(
            id="ch1",
            name="reddit",
            source_type="reddit",
            tos_ruleset_ref="reddit_tos_v1",
            requires_disclosure=True,
            allows_ugc=True,
            active=True,
        )
    )
    s.add(
        ComplianceRule(
            id="cr1",
            channel="*",
            code="no_astroturf",
            description="no fake/undisclosed identities or coordinated inauthentic activity",
            severity="block",
            check_key="no_astroturf",
            active=True,
        )
    )
    s.commit()
    got_channel = s.get(SeedingChannel, "ch1")
    got_rule = s.get(ComplianceRule, "cr1")
    assert got_channel is not None and got_channel.name == "reddit"
    assert got_rule is not None and got_rule.severity == "block"


def test_retrain_job_is_system_level_and_links_drift_event() -> None:
    assert "tenant_id" not in RetrainJob.__table__.columns
    s = _session()
    s.add(
        DriftEvent(
            id="d1",
            engine="gemini",
            canary_id="c1",
            baseline_rate=0.8,
            observed_rate=0.5,
            drop=0.3,
            breached=True,
            retrain_flag=True,
            ts=datetime.now(timezone.utc),
        )
    )
    s.commit()
    s.add(
        RetrainJob(
            id="rj1",
            model_engine="gemini",
            trigger_drift_event_id="d1",
            status="pending",
            metrics_before={"auc": 0.7},
            metrics_after={},
        )
    )
    s.commit()
    got = s.get(RetrainJob, "rj1")
    assert got is not None
    assert got.trigger_drift_event_id == "d1"
    assert got.metrics_after == {}


def test_bandit_arm_and_billing_tables_exist() -> None:
    s = _session()
    s.add(
        EffortBanditArm(
            id="a1",
            tenant_id="t1",
            brand_id="b1",
            arm_key="reddit:v1",
            pulls=0,
            reward_sum=0.0,
            reward_sq_sum=0.0,
        )
    )
    s.add(
        BillingAccount(
            id="acct1",
            tenant_id="t1",
            plan="growth",
            base_fee=500.0,
            usage_rates={"probe": 0.001},
            raas_enabled=False,
            raas_basis="per_lead",
            raas_rate=0.0,
            currency="USD",
        )
    )
    s.commit()
    got_arm = s.scalar(select(EffortBanditArm).where(EffortBanditArm.arm_key == "reddit:v1"))
    assert got_arm is not None
    assert got_arm.pulls == 0


def test_bandit_arm_effort_unique_per_tenant_brand_arm_key() -> None:
    s = _session()
    s.add(
        EffortBanditArm(
            id="a1",
            tenant_id="t1",
            brand_id="b1",
            arm_key="reddit:v1",
            pulls=0,
            reward_sum=0.0,
            reward_sq_sum=0.0,
        )
    )
    s.commit()
    s.add(
        EffortBanditArm(
            id="a2",
            tenant_id="t1",
            brand_id="b1",
            arm_key="reddit:v1",
            pulls=1,
            reward_sum=1.0,
            reward_sq_sum=1.0,
        )
    )
    with pytest.raises(IntegrityError):
        s.commit()


def test_usage_event_and_billing_invoice_roundtrip() -> None:
    s = _session()
    s.add(
        UsageEvent(
            id="u1",
            tenant_id="t1",
            brand_id="b1",
            kind="probe",
            quantity=1.0,
            unit="call",
            source_ref="probe_run:pr1",
        )
    )
    s.add(
        BillingInvoice(
            id="inv1",
            tenant_id="t1",
            period_start="2026-06-01",
            period_end="2026-06-30",
            base_fee=500.0,
            usage_charges={"probe": 12.5},
            raas_charge=0.0,
            attributed_leads=3,
            attributed_pipeline_usd=1500.0,
            total=512.5,
            status="draft",
        )
    )
    s.commit()
    got_usage = s.get(UsageEvent, "u1")
    got_invoice = s.get(BillingInvoice, "inv1")
    assert got_usage is not None
    assert got_usage.kind == "probe"
    assert got_invoice is not None
    assert got_invoice.total == 512.5
