"""Publish connector contract + registry (TRD §9 / PRD §6.4) — keystone interface.

Every CMS/hosting target (WordPress, Webflow, Framer, headless/API, product-hosted subdomain, ...)
implements `PublishConnector`. Adding a new target means writing one connector and calling
`register()`; zero changes to the content pipeline (mirrors the M0 `EngineAdapter` pattern in
`measurement/probe/base.py`).
"""

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from gw_geo.common.models import ContentDraft


class PublishResult(BaseModel):
    published_url: str
    external_id: str
    connector: str


@runtime_checkable
class PublishConnector(Protocol):
    name: str

    async def publish(
        self, draft: ContentDraft, *, freshness: dict[str, Any]
    ) -> PublishResult: ...


_REGISTRY: dict[str, PublishConnector] = {}


def register(connector: PublishConnector) -> None:
    """Register `connector` keyed by its `name`.

    Raises:
        ValueError: a connector with the same `name` is already registered.
    """
    if connector.name in _REGISTRY:
        raise ValueError(f"connector already registered: {connector.name!r}")
    _REGISTRY[connector.name] = connector


def get_connector(name: str) -> PublishConnector:
    """Look up a registered connector by name.

    Raises:
        KeyError: no connector is registered under `name`.
    """
    return _REGISTRY[name]


def all_connectors() -> list[PublishConnector]:
    """Return all currently registered connectors."""
    return list(_REGISTRY.values())


def clear_registry() -> None:
    """Test helper: reset the registry to empty."""
    _REGISTRY.clear()
