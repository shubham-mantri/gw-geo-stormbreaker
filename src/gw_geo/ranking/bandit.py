"""Content-variant x channel bandit (M3-T13, m3-design S2-S4): Thompson sampling over `BanditArm`.

Frames generation/placement as a multi-armed bandit (PRD S6.3): each arm is a
`(content_variant, channel)` pair; reward is the measured mention/citation-rate uplift for that
arm, clamped to `[0, 1]`. Each arm keeps a Beta(alpha, beta) posterior over its true reward rate;
`select` draws one Thompson sample `theta ~ Beta(alpha, beta)` per arm and returns the argmax,
and `update` folds a new measured reward into the winning arm's posterior. `Bandit` itself is a
pure in-memory scheduler -- persistence of `BanditArm` rows to the `bandit_arm` table is the
caller's responsibility (mirrors `ranking/labels.py` / `ranking/dataset.py` staying storage-
agnostic and taking already-loaded domain objects).

RNG is injected (`random.Random`), matching the `capture/antibot.py` convention (`rng: Random |
None = None`, defaulting to a fresh unseeded generator) so callers get deterministic, reproducible
selection under a seed while production use just omits it.
"""

from __future__ import annotations

import random

from gw_geo.common.models import BanditArm


class Bandit:
    """Thompson-sampling scheduler over a fixed set of `BanditArm`s."""

    def __init__(self, arms: list[BanditArm], *, rng: random.Random | None = None) -> None:
        self._arms: dict[str, BanditArm] = {arm.id: arm for arm in arms}
        self._rng = rng if rng is not None else random.Random()

    def select(self) -> BanditArm:
        """Draw `theta ~ Beta(alpha, beta)` per arm and return the arm with the highest draw."""
        best_arm: BanditArm | None = None
        best_theta = -1.0
        for arm in self._arms.values():
            theta = self._rng.betavariate(arm.alpha, arm.beta)
            if theta > best_theta:
                best_theta = theta
                best_arm = arm
        if best_arm is None:
            raise ValueError("Bandit has no arms to select from")
        return best_arm

    def update(self, arm_id: str, reward: float) -> BanditArm:
        """Fold a measured `reward` (clamped to `[0, 1]`) into `arm_id`'s Beta posterior."""
        arm = self._arms[arm_id]
        clamped = max(0.0, min(1.0, reward))
        arm.alpha += clamped
        arm.beta += 1.0 - clamped
        arm.pulls += 1
        return arm

    def arms(self) -> list[BanditArm]:
        """Return the current arms, in the order they were passed to `__init__`."""
        return list(self._arms.values())
