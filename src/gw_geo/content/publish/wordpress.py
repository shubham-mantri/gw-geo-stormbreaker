"""WordPress publishing connector (PRD §6.4, TRD/§9, m3-design §3.5).

Publishes a `ContentDraft` as a WordPress post via the core REST API
(`POST {base_url}/wp-json/wp/v2/posts`), Bearer-authenticated. WordPress's REST API has no native
JSON-LD field, so the merged schema/freshness metadata is embedded as an inline
`<script type="application/ld+json">` block appended to the post body -- the standard way to add
structured data to a WP post without a plugin -- and the freshness dates are additionally sent as
native `date`/`meta` fields so the post's displayed date matches its structured data.

Import is side-effect-free: this module never calls `publish.base.register()` itself --
registration happens at wiring time (`publish.wiring.register_default_connectors`), mirroring the
M0 `measurement.probe` adapters' convention (see `common/wiring.py`).

Known gap: sitemap resubmission (m3-design §3.5, "where applicable") is not implemented here --
no concrete ping/notify contract is specified for T18, and WordPress typically handles its own
sitemap on publish (core since 5.5, or via an SEO plugin) without external prompting. Revisit in
the T22 content pipeline if an explicit resubmission step turns out to be needed.
"""

import json
from typing import Any

import httpx

from gw_geo.common.models import ContentDraft
from gw_geo.content.publish.base import PublishResult

_POSTS_PATH = "/wp-json/wp/v2/posts"
_JSONLD_SCRIPT = '<script type="application/ld+json">{}</script>'


def _render_content(draft: ContentDraft, jsonld: dict[str, Any]) -> str:
    """The WP post body: the draft's markdown, plus its JSON-LD as a trailing inline script tag."""
    if not jsonld:
        return draft.body_markdown
    return f"{draft.body_markdown}\n\n{_JSONLD_SCRIPT.format(json.dumps(jsonld))}"


class WordPressConnector:
    """`PublishConnector` for a self-hosted or WordPress.com blog via the core REST API."""

    name = "wordpress"

    def __init__(
        self, *, base_url: str, token: str, client: httpx.AsyncClient | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = client if client is not None else httpx.AsyncClient()

    async def publish(self, draft: ContentDraft, *, freshness: dict[str, Any]) -> PublishResult:
        """POST `draft` to `{base_url}/wp-json/wp/v2/posts`; raises on a non-2xx response."""
        jsonld: dict[str, Any] = {**draft.schema_jsonld, **freshness}
        response = await self._client.post(
            f"{self._base_url}{_POSTS_PATH}",
            headers={"Authorization": f"Bearer {self._token}"},
            json={
                "title": draft.title,
                "content": _render_content(draft, jsonld),
                "status": "publish",
                "date": freshness.get("datePublished"),
                "meta": {"gw_geo_date_modified": freshness.get("dateModified")},
            },
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return PublishResult(
            published_url=payload["link"], external_id=str(payload["id"]), connector=self.name
        )
