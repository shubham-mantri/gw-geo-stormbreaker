# M4-T07 — Bandit optimizer (UCB1 / Thompson policies)

**Depends on:** M0 models · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** The interpretable bandit that decides where to spend content/seeding effort. Each
**(channel, content_variant)** is an arm; the measurement/attribution signal is the reward (TRD
§6.3/§8, design §3.2). Implement the pure policies + arm math here; persistence-backed allocation and
reward recording land in T14.

**Files:**
- Create: `src/gw_geo/orchestration/bandit.py`
- Test: `tests/orchestration/test_bandit.py`

## Interface (design §3.2)

```python
import math
from typing import Protocol
from pydantic import BaseModel

class Arm(BaseModel):
    key: str                    # f"{channel}:{variant}"
    pulls: int = 0
    reward_sum: float = 0.0
    reward_sq_sum: float = 0.0
    @property
    def mean(self) -> float: ...     # reward_sum / pulls, 0.0 if unpulled

class BanditPolicy(Protocol):
    def rank(self, arms: list[Arm]) -> list[str]: ...   # best-first arm keys

class UCB1Policy:
    def __init__(self, c: float = 1.0) -> None: ...
    def rank(self, arms: list[Arm]) -> list[str]: ...    # unpulled arms first, then UCB score

class ThompsonPolicy:
    def __init__(self, rng: "random.Random | None" = None) -> None: ...
    def rank(self, arms: list[Arm]) -> list[str]: ...    # sample per-arm posterior, sort desc
```

UCB1 score for a pulled arm: `mean + c * sqrt(2 * ln(N) / n_i)` where `N = sum(pulls)`; unpulled arms
(`pulls == 0`) always rank ahead of pulled arms (mandatory exploration). Thompson draws a sample per
arm from a Beta/Normal posterior parameterized by the arm's stats and ranks by the draw; deterministic
under an injected seeded `rng`.

## Steps
- [ ] **1. Failing test** `tests/orchestration/test_bandit.py`:

```python
import random
from gw_geo.orchestration.bandit import Arm, UCB1Policy, ThompsonPolicy

def test_ucb1_explores_unpulled_first():
    arms = [Arm(key="reddit:v1", pulls=10, reward_sum=8.0),   # high mean, well-sampled
            Arm(key="g2:v1", pulls=0, reward_sum=0.0)]        # never tried
    assert UCB1Policy(c=1.0).rank(arms)[0] == "g2:v1"

def test_ucb1_prefers_higher_mean_when_equally_pulled():
    arms = [Arm(key="a", pulls=5, reward_sum=1.0),   # mean 0.2
            Arm(key="b", pulls=5, reward_sum=4.0)]   # mean 0.8
    assert UCB1Policy(c=1.0).rank(arms)[0] == "b"

def test_thompson_is_deterministic_with_seed():
    arms = [Arm(key="a", pulls=20, reward_sum=2.0), Arm(key="b", pulls=20, reward_sum=18.0)]
    r1 = ThompsonPolicy(rng=random.Random(42)).rank(arms)
    r2 = ThompsonPolicy(rng=random.Random(42)).rank(arms)
    assert r1 == r2 and r1[0] == "b"
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `Arm.mean`, `UCB1Policy.rank` (unpulled-first, then UCB score desc),
  `ThompsonPolicy.rank` (seeded sampling). Pure `math`/`random`/`statistics`; no I/O.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(orchestration): UCB1/Thompson bandit policies`

## Acceptance
- UCB1 pulls every unplayed arm before exploiting, then favors higher UCB score; Thompson is
  reproducible under a seeded RNG and favors the higher-reward arm in expectation; pure/hermetic.
