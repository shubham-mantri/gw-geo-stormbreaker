import os  # noqa: F401 -- imported per task spec (M4-T01) verbatim; unused in this test module

from gw_geo.common.config import Settings


def test_m4_defaults():
    s = Settings(_env_file=None)
    assert s.raas_enabled is False
    assert s.raas_basis == "per_lead"
    assert s.bandit_policy == "ucb1"
    assert "reddit" in s.seeding_channels_enabled and "wikipedia" in s.seeding_channels_enabled


def test_m4_env_override(monkeypatch):
    # Settings.model_config sets env_prefix="GEO_" (M0-M3 convention: see test_env_override in
    # test_config.py / test_config_m2.py), so un-prefixed vars like a bare RAAS_ENABLED would be
    # silently ignored. Use the GEO_-prefixed names so the override actually takes effect.
    monkeypatch.setenv("GEO_RAAS_ENABLED", "true")
    monkeypatch.setenv("GEO_BANDIT_POLICY", "thompson")
    s = Settings(_env_file=None)
    assert s.raas_enabled is True and s.bandit_policy == "thompson"
