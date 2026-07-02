"""Tests for the content-variant x channel bandit (M3-T13, m3-design S2-S4).

Thompson sampling over a Beta(alpha, beta) posterior per `BanditArm`: `select` draws
`theta = rng.betavariate(alpha, beta)` per arm and returns the argmax; `update` folds a
measured reward (clamped to `[0, 1]`) into the arm's posterior. Mirrors
`tests/capture/test_antibot.py`'s convention of an injected, seeded `random.Random` for
deterministic assertions on otherwise-random behavior.
"""

import random

from gw_geo.common.models import BanditArm, SourceType
from gw_geo.ranking.bandit import Bandit


def _arm(
    id: str, ch: SourceType = SourceType.REDDIT, a: float = 1.0, b: float = 1.0
) -> BanditArm:
    return BanditArm(
        id=id,
        tenant_id="t1",
        brand_id="b1",
        content_variant=f"v-{id}",
        channel=ch,
        alpha=a,
        beta=b,
    )


def test_update_moves_posterior() -> None:
    b = Bandit([_arm("a1")])
    arm = b.update("a1", reward=1.0)
    assert arm.alpha == 2.0 and arm.beta == 1.0 and arm.pulls == 1
    arm = b.update("a1", reward=0.0)
    assert arm.beta == 2.0 and arm.pulls == 2


def test_reward_clamped() -> None:
    b = Bandit([_arm("a1")])
    arm = b.update("a1", reward=5.0)  # clamp to 1.0
    assert arm.alpha == 2.0


def test_select_prefers_higher_posterior_deterministically() -> None:
    # a1 has strong success history, a2 strong failure; injected rng makes it deterministic
    b = Bandit([_arm("a1", a=50.0, b=1.0), _arm("a2", a=1.0, b=50.0)], rng=random.Random(0))
    picks = [b.select().id for _ in range(20)]
    assert picks.count("a1") > picks.count("a2")


def test_select_frequency_dominates_after_many_successful_updates() -> None:
    # Property test (spec step 4): repeatedly rewarding one arm with 1.0 should concentrate
    # its Beta posterior near 1.0, so it should dominate selection frequency thereafter.
    b = Bandit([_arm("a1"), _arm("a2")], rng=random.Random(42))
    for _ in range(200):
        b.update("a1", reward=1.0)
    picks = [b.select().id for _ in range(200)]
    assert picks.count("a1") > picks.count("a2")
