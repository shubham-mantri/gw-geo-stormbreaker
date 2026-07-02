"""Bandit optimizer: interpretable policies over content/seeding effort (TRD §6.3/§8, design §3.2).

Each **(channel, content_variant)** pair is a bandit `Arm`; the measurement/attribution signal
(mention/citation-rate uplift) is the reward. This module holds only the pure arm math and
ranking policies -- no persistence, no session, no I/O. `orchestration/effort.py` (T14) is the
caller: it upserts `Arm` stats into the `bandit_arm` table (`record_reward`) and turns a
`BanditPolicy.rank(...)` ordering into a concrete slot allocation (`allocate_effort`).

Two policies are provided, both pure functions of already-loaded `Arm`s:

- `UCB1Policy` -- classic upper-confidence-bound selection, with one addition mandated by the
  design: every never-pulled arm ranks ahead of every pulled arm (mandatory exploration), since
  a UCB score for an unpulled arm is otherwise undefined (`n_i == 0`).
- `ThompsonPolicy` -- Beta-Bernoulli posterior sampling, the same conjugate model
  `ranking/bandit.py` (M3) uses for its Thompson-sampling `Bandit`. Rewards are rates in `[0, 1]`
  (PRD §6.3), so `reward_sum` successes out of `pulls` trials plus a flat `Beta(1, 1)` prior
  naturally cold-starts an unpulled arm to `Uniform(0, 1)` -- no special-casing needed, unlike
  UCB1. `reward_sq_sum` is accumulated by `record_reward` (T14) for future variance/confidence
  reporting; it is not needed by this posterior and is intentionally unused here.
"""

from __future__ import annotations

import math
import random
from typing import Protocol

from pydantic import BaseModel

# Non-informative Beta(1, 1) = Uniform(0, 1) prior pseudo-counts, folded into every arm's
# posterior so an unpulled arm samples uniformly instead of raising on a 0/0 mean.
_BETA_PRIOR = 1.0


class Arm(BaseModel):
    """One bandit arm's accumulated pull/reward stats, keyed `f"{channel}:{variant}"`."""

    key: str
    pulls: int = 0
    reward_sum: float = 0.0
    reward_sq_sum: float = 0.0

    @property
    def mean(self) -> float:
        """Average reward per pull, or `0.0` for a never-pulled arm."""
        return self.reward_sum / self.pulls if self.pulls > 0 else 0.0


class BanditPolicy(Protocol):
    """A ranking strategy over a set of arms: best-first arm keys."""

    def rank(self, arms: list[Arm]) -> list[str]: ...


class UCB1Policy:
    """Upper-confidence-bound selection: `mean + c * sqrt(2 * ln(N) / n_i)`, `N = sum(pulls)`.

    Unpulled arms (`pulls == 0`) always rank ahead of pulled arms -- mandatory exploration --
    since the UCB score itself is only defined once an arm has at least one pull.
    """

    def __init__(self, c: float = 1.0) -> None:
        self._c = c

    def rank(self, arms: list[Arm]) -> list[str]:
        unpulled = [arm for arm in arms if arm.pulls == 0]
        pulled = [arm for arm in arms if arm.pulls > 0]
        total_pulls = sum(arm.pulls for arm in arms)

        def _score(arm: Arm) -> float:
            return arm.mean + self._c * math.sqrt(2.0 * math.log(total_pulls) / arm.pulls)

        pulled.sort(key=_score, reverse=True)
        return [arm.key for arm in (*unpulled, *pulled)]


class ThompsonPolicy:
    """Beta-Bernoulli Thompson sampling: draw `theta ~ Beta(1 + successes, 1 + failures)`.

    `successes`/`failures` come from `reward_sum`/`pulls` (rewards are `[0, 1]`-rate signals, so
    `reward_sum` doubles as an accumulated success count). RNG is injected -- a fixed seed makes
    `rank` reproducible; production callers pass an unseeded `random.Random()`.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng if rng is not None else random.Random()

    def rank(self, arms: list[Arm]) -> list[str]:
        def _draw(arm: Arm) -> float:
            successes = min(max(arm.reward_sum, 0.0), float(arm.pulls))
            failures = arm.pulls - successes
            return self._rng.betavariate(_BETA_PRIOR + successes, _BETA_PRIOR + failures)

        ranked = sorted(arms, key=_draw, reverse=True)
        return [arm.key for arm in ranked]
