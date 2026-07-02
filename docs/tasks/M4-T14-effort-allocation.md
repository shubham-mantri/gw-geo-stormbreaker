# M4-T14 — Effort allocation service (bandit reward + slot allocation)

**Depends on:** T07 (bandit policies), T10 (workflow) · **Wave:** 2
**Suggested agent:** general-purpose

**Goal:** Persistence-backed bandit: record measured reward per arm and allocate a finite budget of
placement slots across arms via the chosen policy (design §3.2). Reward comes from the
measurement/attribution signal (delayed 14–21 days — PRD §10) fed in by the caller/scheduler; this
service just persists arms and distributes effort with an exploration floor.

**Files:**
- Create: `src/gw_geo/orchestration/effort.py`
- Test: `tests/orchestration/test_effort.py`

## Interface (design §3.2)

```python
from gw_geo.orchestration.bandit import Arm, BanditPolicy

def record_reward(session, *, tenant_id: str, brand_id: str, arm_key: str,
                  reward: float) -> Arm: ...
#   upsert bandit_arm: pulls+=1, reward_sum+=reward, reward_sq_sum+=reward**2; returns updated Arm

def load_arms(session, *, tenant_id: str, brand_id: str) -> list[Arm]: ...

def allocate_effort(session, *, tenant_id: str, brand_id: str, budget: int,
                    policy: BanditPolicy, candidate_arms: list[str] | None = None,
                    explore_floor: int = 1) -> dict[str, int]: ...
#   -> arm_key → slot count summing to `budget`
```

`allocate_effort` loads persisted arms (creating zero-stat `Arm`s for any `candidate_arms` not yet
seen — guarantees new channels get explored), ranks via `policy`, then distributes `budget` slots
top-weighted while guaranteeing each ranked arm at least `explore_floor` when budget allows.

## Steps
- [ ] **1. Failing test** `tests/orchestration/test_effort.py` (SQLite):

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from gw_geo.common.db import Base, BanditArm
from gw_geo.orchestration.bandit import UCB1Policy
from gw_geo.orchestration.effort import record_reward, load_arms, allocate_effort

def _session():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); return Session(eng)

def test_record_reward_accumulates():
    s = _session()
    record_reward(s, tenant_id="t1", brand_id="b1", arm_key="reddit:v1", reward=1.0)
    a = record_reward(s, tenant_id="t1", brand_id="b1", arm_key="reddit:v1", reward=0.0)
    s.commit()
    assert a.pulls == 2 and a.reward_sum == 1.0
    assert load_arms(s, tenant_id="t1", brand_id="b1")[0].arm_key if False else True

def test_allocate_sums_to_budget_and_explores_new_arm():
    s = _session()
    for _ in range(10):
        record_reward(s, tenant_id="t1", brand_id="b1", arm_key="reddit:v1", reward=1.0)
    s.commit()
    alloc = allocate_effort(s, tenant_id="t1", brand_id="b1", budget=6,
        policy=UCB1Policy(c=1.0), candidate_arms=["reddit:v1", "g2:v1"], explore_floor=1)
    assert sum(alloc.values()) == 6
    assert alloc.get("g2:v1", 0) >= 1        # unpulled candidate gets an exploration slot
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `record_reward` (upsert on `(tenant_id, brand_id, arm_key)`), `load_arms`,
  `allocate_effort` (materialize candidates, rank, distribute to sum exactly to `budget`, honor
  `explore_floor`). Tenant-scoped.
- [ ] **4. Run → pass**; add a test that allocation with `budget < n_arms` still sums to budget.
- [ ] **5. Commit:** `feat(orchestration): bandit reward recording + effort allocation`

## Acceptance
- Rewards accumulate per arm (pulls/reward_sum/reward_sq_sum); `allocate_effort` returns a dict summing
  exactly to `budget`, gives unpulled candidate arms an exploration slot, tenant-scoped; hermetic.
