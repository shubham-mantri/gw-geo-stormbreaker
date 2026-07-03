from gw_geo.common.config import Settings


def test_portkey_gateway_defaults_present():
    s = Settings()
    assert s.llm_gateway == "portkey"
    assert s.portkey_api_key == ""
    assert s.portkey_base_url == "https://api.portkey.ai/v1"
    assert s.portkey_config == "pc-portke-0dd3de"


def test_portkey_env_overrides(monkeypatch):
    monkeypatch.setenv("GEO_PORTKEY_API_KEY", "pk-live")
    monkeypatch.setenv("GEO_LLM_GATEWAY", "direct")
    monkeypatch.setenv("GEO_PORTKEY_CONFIG", "pc-custom-1234")
    s = Settings()
    assert s.portkey_api_key == "pk-live"
    assert s.llm_gateway == "direct"
    assert s.portkey_config == "pc-custom-1234"
