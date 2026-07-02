import httpx
import pytest
import respx

from gw_geo.common.models import ContentDraft
from gw_geo.content.publish.framer import FramerConnector


def _draft():
    return ContentDraft(id="c1", tenant_id="t1", brand_id="b1", title="Best CRM",
                        body_markdown="## A\nAcme wins.", schema_jsonld={"@type": "Article"})


@pytest.mark.asyncio
@respx.mock
async def test_framer_publish_posts_and_returns_url():
    route = respx.post("https://api.framer.com/v1/cms/items").mock(
        return_value=httpx.Response(
            201, json={"id": 7, "url": "https://acme.framer.website/best-crm"}
        )
    )
    async with httpx.AsyncClient() as client:
        conn = FramerConnector(token="secret", client=client)
        res = await conn.publish(
            _draft(), freshness={"datePublished": "2026-07-01", "dateModified": "2026-07-02"}
        )

    assert route.called
    sent = route.calls.last.request
    assert sent.headers["authorization"].lower().startswith("bearer ")
    assert res.published_url == "https://acme.framer.website/best-crm"
    assert res.external_id == "7"
    assert res.connector == "framer"


@pytest.mark.asyncio
@respx.mock
async def test_framer_publish_raises_on_error():
    respx.post("https://api.framer.com/v1/cms/items").mock(
        return_value=httpx.Response(500, json={"message": "internal error"})
    )
    async with httpx.AsyncClient() as client:
        conn = FramerConnector(token="secret", client=client)
        with pytest.raises(Exception):
            await conn.publish(
                _draft(), freshness={"datePublished": "2026-07-01", "dateModified": "2026-07-02"}
            )
