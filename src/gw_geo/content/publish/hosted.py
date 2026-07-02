"""Product-hosted-subdomain publishing connector (PRD §6.4, TRD/§9, m3-design §3.5).

For brands with no CMS of their own: publishes to the product's own hosted knowledge-base
subdomain (`hosted_subdomain_base`, e.g. `kb.example.com`) rather than a third-party provider.
`HostedSubdomainConnector` renders the draft as a small standalone HTML document (JSON-LD embedded
in `<head>`, same reasoning as `wordpress.py` -- raw HTML has no structured-field alternative) and
composes the page's URL deterministically from `subdomain_base` + `brand_id` + a slug, since this
connector -- unlike the third-party ones -- fully owns the target and never needs to hand off to
someone else's ID/link generation.

Storage backend is pluggable and optional, mirroring `common.wiring.S3RawArchive`'s injectable
`client`:

* an injected `object_store` (e.g. an S3-backed store) is used directly when provided -- no
  network call goes through `httpx` at all in that path;
* otherwise the rendered page is PUT to `{subdomain_base}/api/pages/{key}` via the injected
  `httpx.AsyncClient`, the same `respx`-mockable HTTP path every other connector uses, on the
  assumption that our own hosted-KB service exposes a small internal publish API behind that
  subdomain.

Either way, no live network/storage call happens in the hermetic test suite. Import is
side-effect-free: this module never calls `publish.base.register()` itself -- registration
happens at wiring time (`publish.wiring.register_default_connectors`).
"""

import json
import re
from typing import Any, Protocol

import httpx

from gw_geo.common.models import ContentDraft
from gw_geo.content.publish.base import PublishResult

_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")
_PAGE_TEMPLATE = (
    "<!doctype html><html><head><title>{title}</title>"
    '<script type="application/ld+json">{jsonld}</script>'
    "</head><body>{body}</body></html>"
)


def _slugify(title: str) -> str:
    slug = _NON_SLUG_CHARS.sub("-", title.lower()).strip("-")
    return slug or "untitled"


def _render_page(draft: ContentDraft, jsonld: dict[str, Any]) -> str:
    return _PAGE_TEMPLATE.format(
        title=draft.title, jsonld=json.dumps(jsonld), body=draft.body_markdown
    )


class ObjectStore(Protocol):
    """A key/bytes store (e.g. S3). Injected so tests never hit a live bucket."""

    def put(self, key: str, body: bytes, *, content_type: str = "text/html") -> str:
        """Store `body` under `key`; return a reference to the stored object (often just `key`)."""
        ...


class HostedSubdomainConnector:
    """`PublishConnector` for the product's own hosted knowledge-base subdomain."""

    name = "hosted"

    def __init__(
        self,
        *,
        subdomain_base: str,
        client: httpx.AsyncClient | None = None,
        object_store: ObjectStore | None = None,
    ) -> None:
        self._subdomain_base = subdomain_base.strip().rstrip("/")
        self._client = client if client is not None else httpx.AsyncClient()
        self._object_store = object_store

    async def publish(self, draft: ContentDraft, *, freshness: dict[str, Any]) -> PublishResult:
        """Render `draft` to HTML and store it under a URL composed from `subdomain_base`.

        Uses the injected `object_store` when present; otherwise PUTs the page via `httpx` to
        this connector's own publish API and raises on a non-2xx response.
        """
        jsonld: dict[str, Any] = {**draft.schema_jsonld, **freshness}
        key = f"{draft.brand_id}/{_slugify(draft.title)}"
        published_url = f"https://{self._subdomain_base}/{key}"
        html = _render_page(draft, jsonld)

        if self._object_store is not None:
            external_id = self._object_store.put(
                key, html.encode("utf-8"), content_type="text/html"
            )
            return PublishResult(
                published_url=published_url, external_id=external_id, connector=self.name
            )

        response = await self._client.put(
            f"https://{self._subdomain_base}/api/pages/{key}",
            content=html.encode("utf-8"),
            headers={"content-type": "text/html"},
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return PublishResult(
            published_url=published_url,
            external_id=str(payload.get("id", key)),
            connector=self.name,
        )
