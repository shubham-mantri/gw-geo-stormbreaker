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


@lru_cache
def get_settings() -> "Settings":
    return Settings()
