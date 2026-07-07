import pytest
from pydantic import ValidationError

from gw_geo.common import config


def test_defaults_present():
    s = config.Settings()
    assert s.jwt_access_ttl_s == 900
    assert s.cors_allow_origins == ["http://localhost:3000"]
    assert s.hubspot_client_id == ""      # unset = not configured
    assert s.pixel_write_key_salt
    assert s.environment == "development"  # non-prod by default


def test_insecure_defaults_allowed_in_non_production():
    # review fix #3: the dev-default secret/salt are fine outside production (tests + e2e rely on
    # this) -- construction must not raise.
    s = config.Settings()
    assert s.jwt_secret == config._DEV_JWT_SECRET
    assert len(s.jwt_secret.encode()) >= 32  # RFC 7518 §3.2 HMAC-SHA256 key length (no PyJWT warning)
    assert s.pixel_write_key_salt == config._DEV_PIXEL_SALT


def test_insecure_jwt_secret_fails_fast_in_production():
    # review fix #3: shipping the dev JWT secret to production must fail at settings construction.
    with pytest.raises(ValidationError):
        config.Settings(environment="production")


def test_insecure_pixel_salt_fails_fast_in_production():
    # review fix #3: the dev pixel salt must also fail fast in production.
    with pytest.raises(ValidationError):
        config.Settings(
            environment="production",
            jwt_secret="a-real-32-byte-production-secret!!",
        )


def test_production_with_real_secrets_constructs():
    # review fix #3: real secrets in production construct cleanly.
    s = config.Settings(
        environment="production",
        jwt_secret="a-real-32-byte-production-secret!!",
        pixel_write_key_salt="a-real-production-pixel-salt",
    )
    assert s.environment == "production"


def test_env_override(monkeypatch):
    monkeypatch.setenv("GEO_JWT_SECRET", "s3cret")
    monkeypatch.setenv("GEO_GA4_PROPERTY_ID", "properties/123")
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.jwt_secret == "s3cret" and s.ga4_property_id == "properties/123"
