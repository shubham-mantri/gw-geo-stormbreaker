# M1-T01 — Config & secrets (engine keys, proxy/account, drift, playwright)

**Depends on:** M0-T03 (config) · **Wave:** 0 · **Suggested agent:** general-purpose

**Goal:** Extend the typed `Settings` with the new M1 engine keys, capture-fleet config refs, drift
threshold, and Playwright flags — all env-driven, defaulted so hermetic tests need no secrets.

**Files:**
- Modify: `src/gw_geo/common/config.py`
- Test: `tests/common/test_config.py`

## Interface

Add these fields to the existing `Settings` (M0-T03), keeping `env_prefix="GEO_"`:

```python
class Settings(BaseSettings):
    # ... existing M0 fields (database_url, s3_bucket, aws_region,
    #     perplexity_api_key, openai_api_key, anthropic_api_key, default_n_samples, ...) ...

    # M1 API engine keys
    gemini_api_key: str = ""
    copilot_api_key: str = ""
    deepseek_api_key: str = ""
    deepseek_enabled: bool = False          # TRD OT3 — off by default

    # M1 capture-fleet config refs (values resolved from SSM/secret store at deploy)
    proxy_pool_config_ref: str = ""         # e.g. SSM path / secret name
    account_pool_config_ref: str = ""
    playwright_headless: bool = True

    # M1 drift canary
    drift_threshold: float = 0.2
    drift_sns_topic_arn: str = ""
```

`anthropic_api_key` already exists (M0 uses Claude as the parse extractor). Do **not** rename or
remove any M0 field.

## Steps
- [ ] **1. Failing test** — add to `tests/common/test_config.py`:

```python
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
```

- [ ] **2. Run → fail.** `pytest tests/common/test_config.py -v`
- [ ] **3. Implement** the new fields on `Settings` per the interface above.
- [ ] **4. Run → pass**; `mypy src/gw_geo/common` clean.
- [ ] **5. Commit:** `feat(common): m1 engine keys, capture-fleet, drift & playwright settings`

## Acceptance
- All new `GEO_`-prefixed env vars override defaults; every new engine key defaults to `""`;
  `deepseek_enabled` defaults `False`; `drift_threshold` defaults `0.2`; existing M0 fields
  untouched; mypy-strict clean.
