"""Typed, env-driven settings for DB, S3, engine API keys, and default sampling params."""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The insecure development defaults that must never reach production. Kept as the single source of
# truth for both the defaults below and the production fail-fast guard.
_DEV_JWT_SECRET = "dev-insecure-change-me"
_DEV_PIXEL_SALT = "dev-salt"
_PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEO_", env_file=".env", extra="ignore")
    # Deployment environment; anything outside `_PRODUCTION_ENVIRONMENTS` is treated as non-prod
    # (dev/test/CI/e2e), where the insecure defaults below are tolerated.
    environment: str = "development"
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
    jwt_secret: str = _DEV_JWT_SECRET
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
    pixel_write_key_salt: str = _DEV_PIXEL_SALT

    @model_validator(mode="after")
    def _forbid_insecure_defaults_in_production(self) -> "Settings":
        """Fail fast at construction if a production deployment still carries a dev-default secret.

        Shipping the well-known dev JWT secret or pixel salt to production would let anyone forge
        tokens / write-keys, so it must be a hard startup error -- not a silent runtime foot-gun.
        Gated strictly on `environment` so dev/test/CI/e2e (which use the defaults) are unaffected.
        """
        if self.environment.lower() in _PRODUCTION_ENVIRONMENTS:
            insecure = []
            if self.jwt_secret == _DEV_JWT_SECRET:
                insecure.append("GEO_JWT_SECRET")
            if self.pixel_write_key_salt == _DEV_PIXEL_SALT:
                insecure.append("GEO_PIXEL_WRITE_KEY_SALT")
            if insecure:
                raise ValueError(
                    f"insecure development default(s) in production for: {', '.join(insecure)}. "
                    "Set a real secret via the environment before deploying."
                )
        return self


@lru_cache
def get_settings() -> "Settings":
    return Settings()
