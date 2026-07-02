import importlib  # noqa: F401 -- imported per task spec verbatim; unused in this test module
from gw_geo.common import config

def test_env_overrides(monkeypatch):
    monkeypatch.setenv("GEO_DEFAULT_N_SAMPLES", "20")
    config.get_settings.cache_clear()
    assert config.get_settings().default_n_samples == 20

def test_defaults():
    config.get_settings.cache_clear()
    assert config.get_settings().max_probe_concurrency == 8

def test_m1_engine_key_defaults_empty():
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.gemini_api_key == "" and s.copilot_api_key == "" and s.deepseek_api_key == ""
    assert s.deepseek_enabled is False          # gated off (TRD OT3)
    assert s.playwright_headless is True
    assert s.drift_threshold == 0.2

def test_m1_env_overrides(monkeypatch):
    monkeypatch.setenv("GEO_GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("GEO_DEEPSEEK_ENABLED", "true")
    monkeypatch.setenv("GEO_DRIFT_THRESHOLD", "0.35")
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.gemini_api_key == "g-key"
    assert s.deepseek_enabled is True
    assert s.drift_threshold == 0.35
