"""Framer publishing connector (PRD §6.4, TRD/§9, m3-design §3.5).

Publishes a `ContentDraft` to a Framer site's CMS API (`POST /v1/cms/items`),
Bearer-authenticated. Framer's CMS API is a plain JSON API (not a rich-text/structured-field CMS
like Webflow, nor raw HTML like WordPress), so the schema/freshness metadata is sent as nested
JSON alongside the body text -- the most direct mapping for this provider shape. Framer's item
response includes a ready-to-use live `url`, so -- unlike Webflow/hosted -- nothing needs to be
composed locally.

Import is side-effect-free: this module never calls `publish.base.register()` itself --
registration happens at wiring time (`publish.wiring.register_default_connectors`).
"""

from typing import Any

import httpx

from gw_geo.common.models import ContentDraft
from gw_geo.content.publish.base import PublishResult

_API_URL = "https://api.framer.com/v1/cms/items"


class FramerConnector:
    """`PublishConnector` for a Framer site, via its CMS items API."""

    name = "framer"

    def __init__(self, *, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._client = client if client is not None else httpx.AsyncClient()

    async def publish(self, draft: ContentDraft, *, freshness: dict[str, Any]) -> PublishResult:
        """POST `draft` as a new CMS item; raises on a non-2xx response."""
        jsonld: dict[str, Any] = {**draft.schema_jsonld, **freshness}
        response = await self._client.post(
            _API_URL,
            headers={"Authorization": f"Bearer {self._token}"},
            json={
                "title": draft.title,
                "content": draft.body_markdown,
                "schemaJsonLd": jsonld,
                "datePublished": freshness.get("datePublished"),
                "dateModified": freshness.get("dateModified"),
            },
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return PublishResult(
            published_url=payload["url"], external_id=str(payload["id"]), connector=self.name
        )
