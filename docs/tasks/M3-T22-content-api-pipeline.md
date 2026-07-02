# M3-T22 — Content pipeline + `/content` API

**Depends on:** T10, T14, T16, T17, T18 · **Wave:** 3 · **Suggested agent:** general-purpose (integration)

**Goal:** Orchestrate the on-site content pipeline (m3-design §3.6) and expose it as the ui-spec §6
`/content` endpoints. Pipeline: **ground → generate → run_guardrails → persist `content_asset` →
(approval gate) → publish**. The API honors the exact ui-spec shapes and **never publishes without a
passing `GuardrailReport` AND an authorized approval** (gate enforced by T16 + T17). Services are
**injected** into the router; tested with `TestClient` + `dependency_overrides`.

**Files:**
- Create: `src/gw_geo/content/pipeline.py`, `src/gw_geo/api/routers/__init__.py`,
  `src/gw_geo/api/routers/content.py`
- Test: `tests/content/test_pipeline.py`, `tests/api/test_content_api.py`

## Interface

```python
# pipeline.py
from gw_geo.common.models import ContentDraft, GuardrailReport, PublishResult

class ContentService:
    def __init__(self, *, kb, llm, corpus, claim_extractor, voice_scorer, voice_profile,
                 connectors, thresholds=None, id_fn=None) -> None: ...
    def generate(self, *, brand, prompt_text, facts, feature_profile,
                 target_engine=None) -> tuple[ContentDraft, GuardrailReport]: ...
    def approve(self, draft: ContentDraft, *, report: GuardrailReport, role: str) -> ContentDraft: ...
    async def publish(self, draft: ContentDraft, *, connector: str) -> PublishResult: ...
    # publish() calls ensure_publishable(draft) first — raises unless APPROVED

# routers/content.py  (ui-spec §6)
# POST /content/generate  -> {content_id, draft, guardrails:{claims_ok, originality_ok}}
# POST /content/{id}/approve -> {status}
# POST /content/{id}/publish -> {status, published_url}
```

## Steps
- [ ] **1. Failing test** `tests/content/test_pipeline.py` (all fakes; assert the gate holds):

```python
import pytest
from gw_geo.common.models import Brand, Fact, ContentStatus, GuardrailReport
from gw_geo.content.pipeline import ContentService
from gw_geo.content.approval import ApprovalError

# reuse fakes from guardrail/generate tests (StubLLM, WordEmbedder, FakeStore, corpora, scorers)
# ... construct a ContentService with grounded fakes so guardrails pass ...

def _service(passing=True): ...   # helper wiring fakes (grounded claim, clean corpus, good voice)

def test_generate_then_gate_then_publish(monkeypatch):
    svc = _service(passing=True)
    brand = Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com")
    draft, report = svc.generate(brand=brand, prompt_text="best crm",
                                 facts=[Fact(id="f1", brand_id="b1", text="Acme is soc2 certified")],
                                 feature_profile=None)
    assert report.passed is True
    approved = svc.approve(draft, report=report, role="editor")
    assert approved.status == ContentStatus.APPROVED

@pytest.mark.asyncio
async def test_publish_blocked_before_approval():
    svc = _service(passing=True)
    from gw_geo.common.models import ContentDraft
    d = ContentDraft(id="c1", tenant_id="t1", brand_id="b1", title="T", body_markdown="x")
    with pytest.raises(ApprovalError):
        await svc.publish(d, connector="hosted")   # never approved → blocked

def test_failing_guardrails_block_approval():
    svc = _service(passing=False)   # ungrounded claim / plagiarism
    brand = Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com")
    draft, report = svc.generate(brand=brand, prompt_text="best crm", facts=[], feature_profile=None)
    assert report.passed is False
    with pytest.raises(ApprovalError):
        svc.approve(draft, report=report, role="editor")
```

- [ ] **2. Failing test** `tests/api/test_content_api.py` (FastAPI `TestClient`, stub `ContentService`):

```python
from fastapi.testclient import TestClient
from gw_geo.api.app import create_app, Services
from gw_geo.api.deps import Principal, get_principal

class StubContent:
    def generate(self, **kw):
        from gw_geo.common.models import ContentDraft, GuardrailReport
        d = ContentDraft(id="c1", tenant_id="t1", brand_id="b1", title="T", body_markdown="x")
        r = GuardrailReport(originality_ok=True, originality_score=0.1, claims_ok=True,
                            unverified_claims=[], brand_voice_ok=True, brand_voice_score=0.9, passed=True)
        return d, r

def _client(role="editor"):
    app = create_app(Services(content=StubContent()))
    app.dependency_overrides[get_principal] = lambda: Principal(tenant_id="t1", user_id="u1", role=role)
    return TestClient(app)

def test_generate_returns_uispec_shape():
    r = _client().post("/content/generate", json={"brand_id": "b1", "prompt_text": "best crm"})
    body = r.json()
    assert r.status_code == 200
    assert body["content_id"] == "c1"
    assert set(body["guardrails"]) == {"claims_ok", "originality_ok"}     # ui-spec §6
    assert body["guardrails"]["claims_ok"] is True

def test_viewer_cannot_approve():
    r = _client(role="viewer").post("/content/c1/approve", json={})
    assert r.status_code == 403                                          # RBAC gate (ui-spec §5)
```

- [ ] **3. Run → fail.**
- [ ] **4. Implement** `pipeline.py` (`ContentService` composing T14/T16/T17/T18; `publish` calls
  `ensure_publishable` first) and `routers/content.py` (mount in `create_app`; `/approve` + `/publish`
  guarded by `require_role("editor","admin","owner")`; responses match ui-spec §6 exactly). Persist the
  `content_asset` + `content_guardrail_report` on generate.
- [ ] **5. Run → pass**; mypy clean.
- [ ] **6. Commit:** `feat(content): content pipeline + /content API (generate/approve/publish)`

## Acceptance
- `/content/generate` returns `{content_id, draft, guardrails:{claims_ok, originality_ok}}`; approve/
  publish enforce the guardrail + RBAC gate (403 for viewer, `ApprovalError`/blocked without a passing
  report or approval); publish uses a connector; all hermetic.
