import httpx
import pytest
import respx

from gw_geo.common.models import ContentDraft
from gw_geo.content.publish.headless import HeadlessConnector


def _draft():
    return ContentDraft(id="c1", tenant_id="t1", brand_id="b1", title="Best CRM",
                        body_markdown="## A\nAcme wins.", schema_jsonld={"@type": "Article"})


@pytest.mark.asyncio
@respx.mock
async def test_headless_publish_posts_and_returns_url():
    route = respx.post("https://ingest.acme.com/content").mock(
        return_value=httpx.Response(
            200, json={"id": "ext-1", "url": "https://acme.com/blog/best-crm"}
        )
    )
    async with httpx.AsyncClient() as client:
        conn = HeadlessConnector(publish_url="https://ingest.acme.com/content",
                                  token="secret", client=client)
        res = await conn.publish(
            _draft(), freshness={"datePublished": "2026-07-01", "dateModified": "2026-07-02"}
        )

    assert route.called
    sent = route.calls.last.request
    assert sent.headers["authorization"].lower().startswith("bearer ")
    assert res.published_url == "https://acme.com/blog/best-crm"
    assert res.external_id == "ext-1"
    assert res.connector == "headless"


@pytest.mark.asyncio
@respx.mock
async def test_headless_publish_omits_auth_header_without_token():
    route = respx.post("https://ingest.acme.com/content").mock(
        return_value=httpx.Response(200, json={})
    )
    async with httpx.AsyncClient() as client:
        conn = HeadlessConnector(publish_url="https://ingest.acme.com/content", client=client)
        res = await conn.publish(
            _draft(), freshness={"datePublished": "2026-07-01", "dateModified": "2026-07-02"}
        )

    sent = route.calls.last.request
    assert "authorization" not in {k.lower() for k in sent.headers.keys()}
    # No id/url in the response -> falls back to publish_url / draft.id.
    assert res.published_url == "https://ingest.acme.com/content"
    assert res.external_id == "c1"


@pytest.mark.asyncio
@respx.mock
async def test_headless_publish_raises_on_error():
    respx.post("https://ingest.acme.com/content").mock(
        return_value=httpx.Response(400, json={"message": "bad request"})
    )
    async with httpx.AsyncClient() as client:
        conn = HeadlessConnector(publish_url="https://ingest.acme.com/content", client=client)
        with pytest.raises(Exception):
            await conn.publish(
                _draft(), freshness={"datePublished": "2026-07-01", "dateModified": "2026-07-02"}
            )
