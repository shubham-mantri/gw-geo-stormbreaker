from gw_geo.common.config import Settings


def test_gateway_defaults_present():
    s = Settings()
    # M5: local Claude (subscription, $0) is the default; portkey/direct are opt-in via the env.
    assert s.llm_gateway == "local_claude"
    assert s.portkey_api_key == ""
    assert s.portkey_base_url == "https://api.portkey.ai/v1"
    assert s.portkey_config == "pc-portke-0dd3de"


def test_claude_cli_defaults_present():
    s = Settings()
    assert s.claude_cli_bin == "claude"
    assert s.claude_cli_config_dir == "~/.asterisk/Work"
    assert s.claude_cli_model == "sonnet"
    assert s.claude_cli_timeout_s == 300.0


def test_portkey_env_overrides(monkeypatch):
    monkeypatch.setenv("GEO_PORTKEY_API_KEY", "pk-live")
    monkeypatch.setenv("GEO_LLM_GATEWAY", "direct")
    monkeypatch.setenv("GEO_PORTKEY_CONFIG", "pc-custom-1234")
    s = Settings()
    assert s.portkey_api_key == "pk-live"
    assert s.llm_gateway == "direct"
    assert s.portkey_config == "pc-custom-1234"


def test_claude_cli_env_overrides(monkeypatch):
    monkeypatch.setenv("GEO_CLAUDE_CLI_BIN", "/opt/claude")
    monkeypatch.setenv("GEO_CLAUDE_CLI_CONFIG_DIR", "~/.asterisk/Other")
    monkeypatch.setenv("GEO_CLAUDE_CLI_MODEL", "opus")
    monkeypatch.setenv("GEO_CLAUDE_CLI_TIMEOUT_S", "120.5")
    s = Settings()
    assert s.claude_cli_bin == "/opt/claude"
    assert s.claude_cli_config_dir == "~/.asterisk/Other"
    assert s.claude_cli_model == "opus"
    assert s.claude_cli_timeout_s == 120.5
