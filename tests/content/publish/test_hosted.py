import httpx
import pytest
import respx

from gw_geo.common.models import ContentDraft
from gw_geo.content.publish.hosted import HostedSubdomainConnector


def _draft():
    return ContentDraft(id="c1", tenant_id="t1", brand_id="b1", title="Best CRM",
                        body_markdown="## A\nAcme wins.", schema_jsonld={"@type": "Article"})


class _FakeObjectStore:
    def __init__(self):
        self.puts: list[tuple[str, bytes, str]] = []

    def put(self, key, body, *, content_type="text/html"):
        self.puts.append((key, body, content_type))
        return f"stored:{key}"


@pytest.mark.asyncio
async def test_hosted_publish_uses_injected_object_store_with_no_http():
    store = _FakeObjectStore()
    conn = HostedSubdomainConnector(subdomain_base="kb.example.com", object_store=store)

    res = await conn.publish(
        _draft(), freshness={"datePublished": "2026-07-01", "dateModified": "2026-07-02"}
    )

    assert res.published_url == "https://kb.example.com/b1/best-crm"
    assert res.external_id == "stored:b1/best-crm"
    assert res.connector == "hosted"
    assert len(store.puts) == 1
    key, body, content_type = store.puts[0]
    assert key == "b1/best-crm"
    assert content_type == "text/html"
    assert b'"@type": "Article"' in body
    assert b"2026-07-02" in body


@pytest.mark.asyncio
@respx.mock
async def test_hosted_publish_falls_back_to_http_without_object_store():
    route = respx.put("https://kb.example.com/api/pages/b1/best-crm").mock(
        return_value=httpx.Response(200, json={"id": "page-1"})
    )
    async with httpx.AsyncClient() as client:
        conn = HostedSubdomainConnector(subdomain_base="kb.example.com", client=client)
        res = await conn.publish(
            _draft(), freshness={"datePublished": "2026-07-01", "dateModified": "2026-07-02"}
        )

    assert route.called
    assert res.published_url == "https://kb.example.com/b1/best-crm"
    assert res.external_id == "page-1"
    assert res.connector == "hosted"


@pytest.mark.asyncio
@respx.mock
async def test_hosted_publish_raises_on_error_without_object_store():
    respx.put("https://kb.example.com/api/pages/b1/best-crm").mock(
        return_value=httpx.Response(503, json={"message": "unavailable"})
    )
    async with httpx.AsyncClient() as client:
        conn = HostedSubdomainConnector(subdomain_base="kb.example.com", client=client)
        with pytest.raises(Exception):
            await conn.publish(
                _draft(), freshness={"datePublished": "2026-07-01", "dateModified": "2026-07-02"}
            )
