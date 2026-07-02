"""Headless/API publishing connector (PRD §6.4, TRD/§9, m3-design §3.5).

For brands running their own headless CMS or custom frontend: POSTs the draft as a plain JSON
document to a single customer-owned ingestion endpoint (`publish_url`), rather than a named
provider's API. Every field the other connectors adapt to a provider-specific shape is sent here
verbatim (`schema_jsonld` as-is, `freshness` merged in directly), since the receiving system is
unknown and this is the generic, no-assumptions integration surface. Bearer auth is optional --
unlike the named CMS connectors, a headless ingestion endpoint may be secured some other way (a
shared secret header, network allowlisting, ...), so `token` defaults to unset and the
`Authorization` header is only sent when one is configured.

Import is side-effect-free: this module never calls `publish.base.register()` itself --
registration happens at wiring time (`publish.wiring.register_default_connectors`).
"""

from typing import Any

import httpx

from gw_geo.common.models import ContentDraft
from gw_geo.content.publish.base import PublishResult


class HeadlessConnector:
    """`PublishConnector` that POSTs a plain JSON document to a customer-owned `publish_url`."""

    name = "headless"

    def __init__(
        self, *, publish_url: str, token: str = "", client: httpx.AsyncClient | None = None
    ) -> None:
        self._publish_url = publish_url
        self._token = token
        self._client = client if client is not None else httpx.AsyncClient()

    async def publish(self, draft: ContentDraft, *, freshness: dict[str, Any]) -> PublishResult:
        """POST `draft` to `publish_url`; raises on a non-2xx response.

        `published_url`/`external_id` come from the response body when present (`url`/`id`),
        falling back to `publish_url`/`draft.id` for a minimal receiver that echoes nothing back.
        """
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        response = await self._client.post(
            self._publish_url,
            headers=headers,
            json={
                "id": draft.id,
                "title": draft.title,
                "body_markdown": draft.body_markdown,
                "schema_jsonld": draft.schema_jsonld,
                **freshness,
            },
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return PublishResult(
            published_url=str(payload.get("url", self._publish_url)),
            external_id=str(payload.get("id", draft.id)),
            connector=self.name,
        )
