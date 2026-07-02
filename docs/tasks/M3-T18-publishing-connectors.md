# M3-T18 — Publishing connectors (WordPress/Webflow/Framer/headless/hosted)

**Depends on:** T09 · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** Concrete `PublishConnector`s (PRD §6.4) for **WordPress**, **Webflow**, **Framer**,
**headless/API**, and a product-**hosted subdomain**, each conforming to the T09 protocol and injecting
schema/freshness metadata. HTTP via an **injected** `httpx.AsyncClient`, `respx`-mocked in tests —
exactly like the M0/M1 API adapters. Each registers in the T09 registry via `get_connector(name)`.

**Files:**
- Create: `src/gw_geo/content/publish/wordpress.py`, `webflow.py`, `framer.py`, `headless.py`,
  `hosted.py`
- Test: `tests/content/publish/test_wordpress.py`, `tests/content/publish/test_connectors_registry.py`

## Interface

```python
# each connector, e.g. wordpress.py
import httpx
from gw_geo.content.publish.base import PublishConnector, PublishResult
from gw_geo.common.models import ContentDraft

class WordPressConnector:
    name = "wordpress"
    def __init__(self, *, base_url: str, token: str, client: httpx.AsyncClient | None = None) -> None: ...
    async def publish(self, draft: ContentDraft, *, freshness: dict) -> PublishResult: ...
    # POST {base_url}/wp-json/wp/v2/posts with title/content + JSON-LD + datePublished/dateModified

# similarly WebflowConnector, FramerConnector, HeadlessConnector, HostedSubdomainConnector
# HostedSubdomainConnector writes to the product's own hosted KB subdomain (config hosted_subdomain_base)
```

## Steps
- [ ] **1. Failing test** `tests/content/publish/test_wordpress.py` (`respx`-mocked, no live HTTP):

```python
import httpx, pytest, respx
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
```

- [ ] **2. Failing test** `tests/content/publish/test_connectors_registry.py`:

```python
from gw_geo.content.publish import base
from gw_geo.content.publish import wordpress, webflow, framer, headless, hosted

def test_all_connectors_are_protocol_conformant():
    for mod, cls, kw in [
        (wordpress, wordpress.WordPressConnector, dict(base_url="https://b", token="t")),
        (webflow, webflow.WebflowConnector, dict(token="t", site_id="s")),
        (framer, framer.FramerConnector, dict(token="t")),
        (headless, headless.HeadlessConnector, dict(publish_url="https://h")),
        (hosted, hosted.HostedSubdomainConnector, dict(subdomain_base="kb.example.com")),
    ]:
        conn = cls(**kw)
        assert isinstance(conn, base.PublishConnector) and isinstance(conn.name, str)
```

- [ ] **3. Run → fail.**
- [ ] **4. Implement** the five connectors. Each maps the draft (+ `freshness` + `draft.schema_jsonld`)
  to the provider payload via the injected `httpx.AsyncClient`, raises on non-2xx (`response.raise_for_status()`),
  returns `PublishResult`. `HostedSubdomainConnector` composes a URL under `hosted_subdomain_base`
  (may use an injected object store; still hermetic). Provide a `register_default_connectors(settings)`
  helper that registers them by config.
- [ ] **5. Run → pass**; mypy clean.
- [ ] **6. Commit:** `feat(content): CMS publishing connectors (wp/webflow/framer/headless/hosted)`

## Acceptance
- All five connectors conform to `PublishConnector`, publish via injected `respx`-mocked httpx,
  inject schema/freshness metadata, raise on errors, and return a `PublishResult`; no live HTTP.
