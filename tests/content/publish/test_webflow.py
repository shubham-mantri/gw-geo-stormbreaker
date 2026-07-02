import httpx
import pytest
import respx

from gw_geo.common.models import ContentDraft
from gw_geo.content.publish.webflow import WebflowConnector


def _draft():
    return ContentDraft(id="c1", tenant_id="t1", brand_id="b1", title="Best CRM",
                        body_markdown="## A\nAcme wins.", schema_jsonld={"@type": "Article"})


@pytest.mark.asyncio
@respx.mock
async def test_webflow_publish_posts_item_and_returns_composed_url():
    route = respx.post("https://api.webflow.com/v2/collections/site123/items").mock(
        return_value=httpx.Response(
            202, json={"id": "item1", "fieldData": {"slug": "best-crm"}}
        )
    )
    async with httpx.AsyncClient() as client:
        conn = WebflowConnector(token="secret", site_id="site123", client=client)
        res = await conn.publish(
            _draft(), freshness={"datePublished": "2026-07-01", "dateModified": "2026-07-02"}
        )

    assert route.called
    sent = route.calls.last.request
    assert sent.headers["authorization"].lower().startswith("bearer ")
    assert res.published_url == "https://site123.webflow.io/best-crm"
    assert res.external_id == "item1"
    assert res.connector == "webflow"


@pytest.mark.asyncio
@respx.mock
async def test_webflow_publish_raises_on_error():
    respx.post("https://api.webflow.com/v2/collections/site123/items").mock(
        return_value=httpx.Response(422, json={"message": "invalid field"})
    )
    async with httpx.AsyncClient() as client:
        conn = WebflowConnector(token="secret", site_id="site123", client=client)
        with pytest.raises(Exception):
            await conn.publish(
                _draft(), freshness={"datePublished": "2026-07-01", "dateModified": "2026-07-02"}
            )
