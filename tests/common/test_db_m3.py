"""SQLite roundtrip tests for the M3 schema (m3-design §6): content/ranking/opportunity/bandit
tables. Mirrors the M0/M1/M2 `test_db*.py` style -- in-memory SQLite, `Base.metadata.create_all`.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.db import (
    Base,
    BanditArm,
    BanditReward,
    Brand,
    ContentAsset,
    ContentGuardrailReport,
    FeatureModel,
    Opportunity,
    Tenant,
)


def _session() -> Session:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    # Every roundtrip below seeds rows under tenant t1 / brand b1; seed those FK parents once.
    s.add(Tenant(id="t1", name="t", sampling_budget_daily=100.0))
    s.add(Brand(id="b1", tenant_id="t1", name="b", domain="b.com"))
    s.commit()
    return s


def test_content_asset_roundtrip() -> None:
    s = _session()
    s.add(
        ContentAsset(
            id="c1", tenant_id="t1", brand_id="b1", type="onsite", title="Best CRM", status="draft"
        )
    )
    s.commit()
    got = s.get(ContentAsset, "c1")
    assert got is not None and got.tenant_id == "t1" and got.status == "draft"


def test_opportunity_and_feature_model_and_guardrail() -> None:
    s = _session()
    s.add(
        ContentAsset(
            id="c1", tenant_id="t1", brand_id="b1", type="onsite", title="Best CRM", status="draft"
        )
    )
    s.add(
        Opportunity(
            id="o1",
            tenant_id="t1",
            brand_id="b1",
            title="absent on Gemini",
            rationale="0% mention",
            engine="gemini",
            est_impact=0.8,
            source_gap="absence",
            status="open",
        )
    )
    s.add(
        FeatureModel(
            id="m1",
            tenant_id="t1",
            brand_id="b1",
            engine="perplexity",
            model_type="gbt",
            feature_names=["has_schema"],
            importances=[0.4],
            metrics={},
        )
    )
    s.add(
        ContentGuardrailReport(
            id="g1",
            tenant_id="t1",
            content_asset_id="c1",
            originality_ok=True,
            originality_score=0.1,
            claims_ok=True,
            unverified_claims=[],
            brand_voice_ok=True,
            brand_voice_score=0.9,
            passed=True,
        )
    )
    s.commit()
    assert s.get(Opportunity, "o1").source_gap == "absence"
    assert s.get(FeatureModel, "m1").importances == [0.4]
    assert s.get(ContentGuardrailReport, "g1").passed is True


def test_bandit_arm_and_reward_roundtrip() -> None:
    s = _session()
    s.add(
        BanditArm(
            id="a1",
            tenant_id="t1",
            brand_id="b1",
            content_variant="v1",
            channel="onsite",
            alpha=1.0,
            beta=1.0,
            pulls=0,
        )
    )
    s.commit()
    s.add(
        BanditReward(
            id="r1",
            tenant_id="t1",
            arm_id="a1",
            reward=1.0,
            source_snapshot_id="snap1",
        )
    )
    s.commit()
    got_arm = s.get(BanditArm, "a1")
    got_reward = s.get(BanditReward, "r1")
    assert got_arm is not None and got_arm.pulls == 0
    assert got_reward is not None and got_reward.arm_id == "a1" and got_reward.reward == 1.0
