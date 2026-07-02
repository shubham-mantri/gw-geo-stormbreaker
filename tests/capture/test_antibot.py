"""Tests for anti-bot fingerprint helpers (docs/tasks/M1-T10-account-pool.md).

`pick_user_agent` / `jitter_delay` are pure functions; a seeded `random.Random` makes them
fully deterministic, so these tests never depend on real timing, network, or a real browser.
"""

import random

from gw_geo.capture.antibot import jitter_delay, pick_user_agent


def test_pick_user_agent_deterministic_under_seeded_rng():
    a = pick_user_agent("chatgpt", rng=random.Random(42))
    b = pick_user_agent("chatgpt", rng=random.Random(42))
    assert a == b


def test_pick_user_agent_returns_plausible_value():
    ua = pick_user_agent("chatgpt", rng=random.Random(1))
    assert ua.startswith("Mozilla/5.0")
    assert "AppleWebKit" in ua


def test_pick_user_agent_unknown_surface_falls_back_without_raising():
    ua = pick_user_agent("some_future_surface", rng=random.Random(1))
    assert ua.startswith("Mozilla/5.0")


def test_jitter_delay_deterministic_under_seeded_rng():
    a = jitter_delay(500, rng=random.Random(7))
    b = jitter_delay(500, rng=random.Random(7))
    assert a == b


def test_jitter_delay_stays_within_plausible_bounds():
    delay = jitter_delay(500, rng=random.Random(7))
    assert 350.0 <= delay <= 650.0  # base_ms +/- 30%


def test_jitter_delay_never_goes_negative_for_small_base():
    delay = jitter_delay(1, rng=random.Random(3))
    assert 0.0 <= delay <= 11.0  # spread floored at 10ms for a near-zero base
