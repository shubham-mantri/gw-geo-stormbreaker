# M2-T01 — Config & secrets (auth, CORS, integrations, pixel)

**Depends on:** M0 config · **Wave:** 0 · **Suggested agent:** general-purpose

**Goal:** Extend `Settings` (TRD §7, m2-design §8) with everything the API layer, auth, integrations
and pixel need. Env-driven, typed, testable; no secrets in repo.

**Files:**
- Edit: `src/gw_geo/common/config.py`
- Test: `tests/common/test_config_m2.py`

## Interface (add to `Settings`)

```python
# JWT / auth
jwt_secret: str = "dev-insecure-change-me"
jwt_access_ttl_s: int = 900
jwt_refresh_ttl_s: int = 1209600          # 14d
# API
cors_allow_origins: list[str] = ["http://localhost:3000"]
# integrations (secrets via env/SSM; blank default = "not configured")
hubspot_client_id: str = ""
hubspot_client_secret: str = ""
salesforce_client_id: str = ""
salesforce_client_secret: str = ""
ga4_property_id: str = ""
ga4_credentials_ref: str = ""             # SSM/secret ref, never inline creds
# lead-capture pixel
pixel_write_key_salt: str = "dev-salt"
```

## Steps
- [ ] **1. Failing test** `tests/common/test_config_m2.py`:

```python
import importlib
from gw_geo.common import config

def test_defaults_present():
    s = config.Settings()
    assert s.jwt_access_ttl_s == 900
    assert s.cors_allow_origins == ["http://localhost:3000"]
    assert s.hubspot_client_id == ""      # unset = not configured
    assert s.pixel_write_key_salt

def test_env_override(monkeypatch):
    monkeypatch.setenv("GEO_JWT_SECRET", "s3cret")
    monkeypatch.setenv("GEO_GA4_PROPERTY_ID", "properties/123")
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.jwt_secret == "s3cret" and s.ga4_property_id == "properties/123"
```

- [ ] **2. Run → fail.** `pytest tests/common/test_config_m2.py -v`
- [ ] **3. Implement** the new fields (env prefix `GEO_` already set). Keep `get_settings` `lru_cache`.
- [ ] **4. Run → pass**; `mypy src/gw_geo/common` clean.
- [ ] **5. Commit:** `feat(config): auth/CORS/integration/pixel settings for M2`

## Acceptance
- New fields exist with the exact names/types above; env override works via `GEO_` prefix; secrets
  default blank (never committed); mypy-strict clean.
