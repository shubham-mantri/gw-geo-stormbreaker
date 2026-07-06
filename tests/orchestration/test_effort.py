"""Effort allocation service tests (M4-T14, `docs/tasks/M4-T14-effort-allocation.md`).

Hermetic (TRD §12): in-memory SQLite, no live DB/network. `record_reward` persists to
`bandit_arm_effort` (`db.EffortBanditArm`) -- deliberately distinct from M3's `db.BanditArm`
(`bandit_arm`), a different Thompson content-variant bandit; see `db.EffortBanditArm`'s
docstring for the rationale. Assertions here go through the pydantic `Arm`
(`orchestration.bandit.Arm`) returned by `record_reward`/`load_arms`, not the ORM row, so no
`db.EffortBanditArm` import is needed here.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.db import Base, Brand, Tenant
from gw_geo.orchestration.bandit import UCB1Policy
from gw_geo.orchestration.effort import allocate_effort, load_arms, record_reward


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    # FK parents for the EffortBanditArm rows record_reward persists (-> tenant, brand).
    session.add(Tenant(id="t1", name="t", sampling_budget_daily=100.0))
    session.add(Brand(id="b1", tenant_id="t1", name="b", domain="b.com"))
    session.commit()
    return session


def test_record_reward_accumulates() -> None:
    session = _session()
    record_reward(session, tenant_id="t1", brand_id="b1", arm_key="reddit:v1", reward=1.0)
    arm = record_reward(session, tenant_id="t1", brand_id="b1", arm_key="reddit:v1", reward=0.0)
    session.commit()

    assert arm.pulls == 2
    assert arm.reward_sum == 1.0
    assert arm.reward_sq_sum == 1.0

    arms = load_arms(session, tenant_id="t1", brand_id="b1")
    assert len(arms) == 1
    assert arms[0].key == "reddit:v1"
    assert arms[0].pulls == 2


def test_allocate_sums_to_budget_and_explores_new_arm() -> None:
    session = _session()
    for _ in range(10):
        record_reward(session, tenant_id="t1", brand_id="b1", arm_key="reddit:v1", reward=1.0)
    session.commit()

    alloc = allocate_effort(
        session,
        tenant_id="t1",
        brand_id="b1",
        budget=6,
        policy=UCB1Policy(c=1.0),
        candidate_arms=["reddit:v1", "g2:v1"],
        explore_floor=1,
    )

    assert sum(alloc.values()) == 6
    assert alloc.get("g2:v1", 0) >= 1  # unpulled candidate gets an exploration slot


def test_allocate_with_budget_below_arm_count_still_sums_to_budget() -> None:
    session = _session()
    candidate_arms = [f"channel-{i}:v1" for i in range(5)]

    alloc = allocate_effort(
        session,
        tenant_id="t1",
        brand_id="b1",
        budget=2,
        policy=UCB1Policy(c=1.0),
        candidate_arms=candidate_arms,
        explore_floor=1,
    )

    assert sum(alloc.values()) == 2
    assert len(alloc) <= 2
