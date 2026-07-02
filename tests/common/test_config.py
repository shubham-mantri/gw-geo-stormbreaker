import importlib  # noqa: F401 -- imported per task spec verbatim; unused in this test module
from gw_geo.common import config
from gw_geo.common.config import Settings

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

def test_m3_defaults_present():
    s = Settings()
    assert s.vector_store == "pinecone"
    assert s.ranking_model_type in {"gbt", "logreg"}
    assert 0.0 < s.originality_threshold < 1.0
    assert s.claim_sim_threshold == 0.8
    assert s.brand_voice_min == 0.7
    assert s.hosted_subdomain_base.endswith(".com")

def test_m3_env_override(monkeypatch):
    monkeypatch.setenv("GEO_RANKING_MODEL_TYPE", "logreg")
    monkeypatch.setenv("GEO_ORIGINALITY_THRESHOLD", "0.15")
    s = Settings()
    assert s.ranking_model_type == "logreg"
    assert s.originality_threshold == 0.15
