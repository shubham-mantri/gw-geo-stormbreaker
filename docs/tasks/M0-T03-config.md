# M0-T03 — Config (pydantic-settings)

**Depends on:** none · **Wave:** 0 · **Suggested agent:** general-purpose

**Goal:** Typed, env-driven settings for DB, S3, engine API keys, and default sampling params.

**Files:**
- Create: `src/gw_geo/common/config.py`
- Test: `tests/common/test_config.py`

## Interface

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEO_", env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://localhost/geo_dev"
    s3_bucket: str = "gw-geo-dev"
    aws_region: str = "us-east-1"
    perplexity_api_key: str = ""
    openai_api_key: str = ""
    default_n_samples: int = 8
    default_geos: list[str] = ["us"]
    max_probe_concurrency: int = 8

@lru_cache
def get_settings() -> "Settings":
    return Settings()
```

## Steps
- [ ] **1. Failing test** `tests/common/test_config.py`:

```python
import importlib
from gw_geo.common import config

def test_env_overrides(monkeypatch):
    monkeypatch.setenv("GEO_DEFAULT_N_SAMPLES", "20")
    config.get_settings.cache_clear()
    assert config.get_settings().default_n_samples == 20

def test_defaults():
    config.get_settings.cache_clear()
    assert config.get_settings().max_probe_concurrency == 8
```

- [ ] **2. Run → fail.** `pytest tests/common/test_config.py -v`
- [ ] **3. Implement** `config.py` per interface.
- [ ] **4. Run → pass**; `mypy src/gw_geo/common` clean.
- [ ] **5. Commit:** `feat(common): add typed settings`

## Acceptance
- `GEO_`-prefixed env vars override defaults; `get_settings()` is cached; mypy-strict clean.
