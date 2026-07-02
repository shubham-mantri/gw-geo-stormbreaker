"""Webflow publishing connector (PRD §6.4, TRD/§9, m3-design §3.5).

Publishes a `ContentDraft` as a Webflow CMS collection item (`POST
/collections/{site_id}/items`), Bearer-authenticated. Unlike WordPress's raw-HTML body, Webflow
CMS items are structured field data, so the schema/freshness metadata is sent as its own
`schema-jsonld` field alongside the human-readable `post-body`, rather than inlined as a script
tag (a Webflow rich-text field would render a literal `<script>` tag as escaped text, not execute
it). Webflow's item-creation response does not include the item's live URL, so `published_url` is
composed from `site_id` + the (possibly server-adjusted) slug -- the same "compose the URL"
approach `HostedSubdomainConnector` uses, since neither platform hands back a ready-made link.

Import is side-effect-free: this module never calls `publish.base.register()` itself --
registration happens at wiring time (`publish.wiring.register_default_connectors`).
"""

import json
import re
from typing import Any

import httpx

from gw_geo.common.models import ContentDraft
from gw_geo.content.publish.base import PublishResult

_API_BASE = "https://api.webflow.com/v2"
_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def _slugify(title: str) -> str:
    slug = _NON_SLUG_CHARS.sub("-", title.lower()).strip("-")
    return slug or "untitled"


class WebflowConnector:
    """`PublishConnector` for a Webflow site's CMS, via the v2 Collection Items API."""

    name = "webflow"

    def __init__(
        self, *, token: str, site_id: str, client: httpx.AsyncClient | None = None
    ) -> None:
        self._token = token
        self._site_id = site_id
        self._client = client if client is not None else httpx.AsyncClient()

    async def publish(self, draft: ContentDraft, *, freshness: dict[str, Any]) -> PublishResult:
        """POST `draft` as a new CMS item; raises on a non-2xx response."""
        slug = _slugify(draft.title)
        jsonld: dict[str, Any] = {**draft.schema_jsonld, **freshness}
        response = await self._client.post(
            f"{_API_BASE}/collections/{self._site_id}/items",
            headers={"Authorization": f"Bearer {self._token}"},
            json={
                "isArchived": False,
                "isDraft": False,
                "fieldData": {
                    "name": draft.title,
                    "slug": slug,
                    "post-body": draft.body_markdown,
                    "schema-jsonld": json.dumps(jsonld),
                    "date-published": freshness.get("datePublished"),
                    "date-modified": freshness.get("dateModified"),
                },
            },
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        field_data: dict[str, Any] = payload.get("fieldData", {})
        published_slug = field_data.get("slug", slug)
        return PublishResult(
            published_url=f"https://{self._site_id}.webflow.io/{published_slug}",
            external_id=str(payload["id"]),
            connector=self.name,
        )
