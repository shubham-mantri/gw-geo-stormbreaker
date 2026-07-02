"""Typed, env-driven settings for DB, S3, engine API keys, and default sampling params."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEO_", env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://localhost/geo_dev"
    s3_bucket: str = "gw-geo-dev"
    aws_region: str = "us-east-1"
    perplexity_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    default_n_samples: int = 8
    default_geos: list[str] = ["us"]
    max_probe_concurrency: int = 8

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

    # M2 JWT / auth
    jwt_secret: str = "dev-insecure-change-me"
    jwt_access_ttl_s: int = 900
    jwt_refresh_ttl_s: int = 1209600          # 14d

    # M2 API
    cors_allow_origins: list[str] = ["http://localhost:3000"]

    # M2 integrations (secrets via env/SSM; blank default = "not configured")
    hubspot_client_id: str = ""
    hubspot_client_secret: str = ""
    salesforce_client_id: str = ""
    salesforce_client_secret: str = ""
    ga4_property_id: str = ""
    ga4_credentials_ref: str = ""             # SSM/secret ref, never inline creds

    # M2 lead-capture pixel
    pixel_write_key_salt: str = "dev-salt"


@lru_cache
def get_settings() -> "Settings":
    return Settings()
