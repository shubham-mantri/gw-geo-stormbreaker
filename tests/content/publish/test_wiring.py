"""`register_default_connectors` wiring tests (mirrors `tests/test_wiring.py`'s `build_runtime`
coverage for the measurement-engine registry): config-gated registration + idempotency.

Hermetic: no live HTTP. `HostedSubdomainConnector`/etc. construct an `httpx.AsyncClient`, but no
request is made anywhere in this module -- only construction/registration is exercised.
"""

from collections.abc import Iterator

import pytest

from gw_geo.common.config import Settings
from gw_geo.content.publish import base
from gw_geo.content.publish.wiring import register_default_connectors

_PUBLISH_ENV_VARS = (
    "GEO_WORDPRESS_BASE_URL",
    "GEO_WORDPRESS_TOKEN",
    "GEO_WEBFLOW_TOKEN",
    "GEO_WEBFLOW_SITE_ID",
    "GEO_FRAMER_TOKEN",
    "GEO_HEADLESS_PUBLISH_URL",
)


@pytest.fixture(autouse=True)
def _hermetic_publish_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate `Settings()` from ambient `GEO_*` publish env vars and the global registry."""
    for var in _PUBLISH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield
    base.clear_registry()


def test_registers_only_hosted_by_default():
    """Third-party CMS creds default to blank (skipped); the hosted fallback is always on."""
    registered = register_default_connectors(Settings())

    assert registered == ["hosted"]
    assert {c.name for c in base.all_connectors()} == {"hosted"}


def test_registers_all_five_when_fully_configured():
    settings = Settings(
        wordpress_base_url="https://blog.acme.com",
        wordpress_token="wp-token",
        webflow_token="wf-token",
        webflow_site_id="site123",
        framer_token="fr-token",
        headless_publish_url="https://ingest.acme.com/content",
    )

    registered = register_default_connectors(settings)

    assert set(registered) == {"wordpress", "webflow", "framer", "headless", "hosted"}
    assert {c.name for c in base.all_connectors()} == set(registered)


def test_partial_credentials_skip_the_connector():
    """A connector needing two config values is skipped unless *both* are set."""
    settings = Settings(
        wordpress_base_url="https://blog.acme.com",  # token missing
        webflow_site_id="site123",  # token missing
    )

    registered = register_default_connectors(settings)

    assert "wordpress" not in registered
    assert "webflow" not in registered
    assert registered == ["hosted"]


def test_register_default_connectors_is_idempotent_across_calls():
    """A warm process calling this twice must rebuild, not raise on duplicate names (C1-style)."""
    settings = Settings(framer_token="fr-token")

    first = register_default_connectors(settings)
    second = register_default_connectors(settings)

    assert first == second == ["framer", "hosted"]
    assert {c.name for c in base.all_connectors()} == {"framer", "hosted"}
