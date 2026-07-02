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
