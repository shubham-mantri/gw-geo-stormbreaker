import importlib  # noqa: F401 -- imported per task spec verbatim; unused in this test module
from gw_geo.common import config

def test_env_overrides(monkeypatch):
    monkeypatch.setenv("GEO_DEFAULT_N_SAMPLES", "20")
    config.get_settings.cache_clear()
    assert config.get_settings().default_n_samples == 20

def test_defaults():
    config.get_settings.cache_clear()
    assert config.get_settings().max_probe_concurrency == 8
