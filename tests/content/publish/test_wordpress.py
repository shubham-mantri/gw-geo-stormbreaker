import httpx
import pytest
import respx

from gw_geo.common.models import ContentDraft
from gw_geo.content.publish.wordpress import WordPressConnector


def _draft():
    return ContentDraft(id="c1", tenant_id="t1", brand_id="b1", title="Best CRM",
                        body_markdown="## A\nAcme wins.", schema_jsonld={"@type": "Article"})


@pytest.mark.asyncio
@respx.mock
async def test_wordpress_publish_posts_and_returns_url():
    route = respx.post("https://blog.acme.com/wp-json/wp/v2/posts").mock(
        return_value=httpx.Response(201, json={"id": 42, "link": "https://blog.acme.com/best-crm"}))
    async with httpx.AsyncClient() as client:
        conn = WordPressConnector(base_url="https://blog.acme.com", token="secret", client=client)
        res = await conn.publish(_draft(), freshness={"datePublished": "2026-07-01",
                                                       "dateModified": "2026-07-02"})
    assert route.called
    sent = route.calls.last.request
    assert sent.headers["authorization"].lower().startswith("bearer ")
    assert res.published_url == "https://blog.acme.com/best-crm"
    assert res.external_id == "42" and res.connector == "wordpress"


@pytest.mark.asyncio
@respx.mock
async def test_wordpress_publish_raises_on_error():
    respx.post("https://blog.acme.com/wp-json/wp/v2/posts").mock(
        return_value=httpx.Response(403, json={"message": "forbidden"}))
    async with httpx.AsyncClient() as client:
        conn = WordPressConnector(base_url="https://blog.acme.com", token="x", client=client)
        with pytest.raises(Exception):
            await conn.publish(_draft(), freshness={"datePublished": "2026-07-01",
                                                    "dateModified": "2026-07-02"})
