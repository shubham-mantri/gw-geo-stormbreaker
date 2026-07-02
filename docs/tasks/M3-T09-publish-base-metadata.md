# M3-T09 — Publish connector base + schema/freshness metadata

**Depends on:** T03 · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** The stable publishing interface every CMS connector implements (mirrors the M0
`EngineAdapter` keystone pattern), plus the **schema/freshness metadata** builder (PRD §6.4):
JSON-LD (Article/FAQPage) + `datePublished`/`dateModified`. Concrete connectors (T18) plug in behind
this. Get it right — T18 and the content pipeline (T22) depend on it.

**Files:**
- Create: `src/gw_geo/content/publish/__init__.py`, `src/gw_geo/content/publish/base.py`,
  `src/gw_geo/content/publish/metadata.py`
- Test: `tests/content/publish/test_base.py`, `tests/content/publish/test_metadata.py`,
  `tests/content/publish/__init__.py`

## Interface

```python
# base.py
from typing import Any, Protocol, runtime_checkable
from pydantic import BaseModel
from gw_geo.common.models import ContentDraft

class PublishResult(BaseModel):
    published_url: str
    external_id: str
    connector: str

@runtime_checkable
class PublishConnector(Protocol):
    name: str
    async def publish(self, draft: ContentDraft, *, freshness: dict[str, Any]) -> PublishResult: ...

_REGISTRY: dict[str, PublishConnector] = {}
def register(connector: PublishConnector) -> None: ...   # dup name → ValueError
def get_connector(name: str) -> PublishConnector: ...    # unknown → KeyError
def clear_registry() -> None: ...

# metadata.py
def build_jsonld(draft: ContentDraft, *, published: str, modified: str) -> dict[str, Any]: ...
def freshness_meta(published: str, modified: str) -> dict[str, Any]: ...   # datePublished/dateModified
```

## Steps
- [ ] **1. Failing test** `tests/content/publish/test_base.py`:

```python
import pytest
from gw_geo.common.models import ContentDraft
from gw_geo.content.publish import base

class FakeConnector:
    name = "fake"
    async def publish(self, draft, *, freshness):
        return base.PublishResult(published_url="https://x/1", external_id="1", connector="fake")

def test_register_get_and_protocol():
    base.clear_registry(); c = FakeConnector(); base.register(c)
    assert base.get_connector("fake") is c
    assert isinstance(c, base.PublishConnector)

def test_duplicate_and_unknown():
    base.clear_registry(); base.register(FakeConnector())
    with pytest.raises(ValueError): base.register(FakeConnector())
    with pytest.raises(KeyError): base.get_connector("nope")
```

- [ ] **2. Failing test** `tests/content/publish/test_metadata.py`:

```python
from gw_geo.common.models import ContentDraft
from gw_geo.content.publish.metadata import build_jsonld, freshness_meta

def _draft():
    return ContentDraft(id="c1", tenant_id="t1", brand_id="b1", title="Best CRM",
                        body_markdown="## Q\nA")

def test_freshness_fields():
    m = freshness_meta("2026-07-01", "2026-07-02")
    assert m["datePublished"] == "2026-07-01" and m["dateModified"] == "2026-07-02"

def test_jsonld_has_schema_type_and_dates():
    ld = build_jsonld(_draft(), published="2026-07-01", modified="2026-07-02")
    assert ld["@context"] == "https://schema.org"
    assert ld["@type"] in {"Article", "FAQPage"}
    assert ld["headline"] == "Best CRM"
    assert ld["dateModified"] == "2026-07-02"
```

- [ ] **3. Run → fail.**
- [ ] **4. Implement** `base.py` (registry mirroring `measurement/probe/base.py`) and `metadata.py`
  (JSON-LD builder; `@type` = `FAQPage` when the draft body/schema indicates Q&A, else `Article`).
- [ ] **5. Run → pass**; mypy clean.
- [ ] **6. Commit:** `feat(content): publish connector protocol + registry + schema/freshness metadata`

## Acceptance
- `PublishConnector` is a runtime-checkable Protocol with a register/get/clear registry (dup→ValueError,
  unknown→KeyError); `build_jsonld`/`freshness_meta` emit valid schema.org + freshness fields.
