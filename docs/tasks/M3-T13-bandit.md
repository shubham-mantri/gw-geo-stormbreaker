# M3-T13 — Bandit (content-variant × channel, reward = measurement)

**Depends on:** T03, T02 · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** Frame generation/placement as a **bandit** (PRD §6.3): each **arm = (content_variant,
channel)**; **reward = measurement** (mention/citation-rate uplift ∈ [0,1]). Thompson sampling over a
Beta posterior per arm; `select()` proposes the next arm, `update(arm_id, reward)` folds in the
measured result. RNG is **injected** for deterministic tests. Arms persist to `bandit_arm`.

**Files:**
- Create: `src/gw_geo/ranking/bandit.py`
- Test: `tests/ranking/test_bandit.py`

## Interface

```python
from gw_geo.common.models import BanditArm

class Bandit:
    def __init__(self, arms: list[BanditArm], *, rng=None) -> None: ...   # rng: random.Random
    def select(self) -> BanditArm: ...            # Thompson sample θ~Beta(alpha,beta), argmax
    def update(self, arm_id: str, reward: float) -> BanditArm: ...
    # reward clamped to [0,1]; alpha += reward, beta += (1-reward), pulls += 1
    def arms(self) -> list[BanditArm]: ...
```

## Steps
- [ ] **1. Failing test** `tests/ranking/test_bandit.py`:

```python
import random
from gw_geo.common.models import BanditArm, SourceType
from gw_geo.ranking.bandit import Bandit

def _arm(id, ch=SourceType.REDDIT, a=1.0, b=1.0):
    return BanditArm(id=id, tenant_id="t1", brand_id="b1", content_variant=f"v-{id}",
                     channel=ch, alpha=a, beta=b)

def test_update_moves_posterior():
    b = Bandit([_arm("a1")])
    arm = b.update("a1", reward=1.0)
    assert arm.alpha == 2.0 and arm.beta == 1.0 and arm.pulls == 1
    arm = b.update("a1", reward=0.0)
    assert arm.beta == 2.0 and arm.pulls == 2

def test_reward_clamped():
    b = Bandit([_arm("a1")])
    arm = b.update("a1", reward=5.0)   # clamp to 1.0
    assert arm.alpha == 2.0

def test_select_prefers_higher_posterior_deterministically():
    # a1 has strong success history, a2 strong failure; injected rng makes it deterministic
    b = Bandit([_arm("a1", a=50.0, b=1.0), _arm("a2", a=1.0, b=50.0)], rng=random.Random(0))
    picks = [b.select().id for _ in range(20)]
    assert picks.count("a1") > picks.count("a2")
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `bandit.py`. `select` draws `theta = rng.betavariate(alpha, beta)` per arm and
  returns the argmax; `update` clamps reward to [0,1] and updates the arm's Beta params + `pulls`.
- [ ] **4. Run → pass**; add a property test: after many `update(arm, 1.0)` calls, that arm's
  selection frequency dominates.
- [ ] **5. Commit:** `feat(ranking): thompson-sampling bandit over variant×channel arms`

## Acceptance
- Reward-driven posterior updates are correct and clamped; `select` is deterministic under an injected
  RNG and favors higher-posterior arms; arms round-trip to `BanditArm`.
