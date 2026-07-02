"""Bandit policy tests (M4-T07, `docs/tasks/M4-T07-bandit.md`).

Hermetic (TRD §12): `Arm`/`UCB1Policy`/`ThompsonPolicy` are pure in-memory math over already-
constructed `Arm`s -- no DB/HTTP/LLM calls. Persistence-backed reward recording and effort
allocation land in T14 (`orchestration/effort.py`).
"""

from __future__ import annotations

import random

from gw_geo.orchestration.bandit import Arm, ThompsonPolicy, UCB1Policy


def test_ucb1_explores_unpulled_first() -> None:
    arms = [
        Arm(key="reddit:v1", pulls=10, reward_sum=8.0),  # high mean, well-sampled
        Arm(key="g2:v1", pulls=0, reward_sum=0.0),  # never tried
    ]
    assert UCB1Policy(c=1.0).rank(arms)[0] == "g2:v1"


def test_ucb1_prefers_higher_mean_when_equally_pulled() -> None:
    arms = [
        Arm(key="a", pulls=5, reward_sum=1.0),  # mean 0.2
        Arm(key="b", pulls=5, reward_sum=4.0),  # mean 0.8
    ]
    assert UCB1Policy(c=1.0).rank(arms)[0] == "b"


def test_thompson_is_deterministic_with_seed() -> None:
    arms = [Arm(key="a", pulls=20, reward_sum=2.0), Arm(key="b", pulls=20, reward_sum=18.0)]
    r1 = ThompsonPolicy(rng=random.Random(42)).rank(arms)
    r2 = ThompsonPolicy(rng=random.Random(42)).rank(arms)
    assert r1 == r2 and r1[0] == "b"
