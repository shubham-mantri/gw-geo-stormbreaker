# M3-T21 — `/opportunities` API (list + act → content)

**Depends on:** T10, T19, T22 · **Wave:** 3 (last) · **Suggested agent:** general-purpose (integration)

**Goal:** Expose the Opportunities queue (ui-spec §3.4, §6): list ranked gaps, and the **`act` →
content** flow ("Fix this ▸") that spawns a pre-scoped draft via the content pipeline (T22). Response
shapes match ui-spec §6 exactly. Services injected; tested with `TestClient` + `dependency_overrides`.

**Files:**
- Create: `src/gw_geo/api/routers/opportunities.py`
- Edit: `src/gw_geo/api/app.py` (mount the router)
- Test: `tests/api/test_opportunities_api.py`

## Interface

```python
# routers/opportunities.py  (ui-spec §6)
# GET  /brands/{id}/opportunities        -> [{id,title,rationale,est_impact,engine}]
# POST /opportunities/{id}/act           -> {content_id}

class OpportunityService(Protocol):
    def list_for_brand(self, *, tenant_id: str, brand_id: str) -> list[dict]: ...
    def act(self, *, tenant_id: str, opportunity_id: str) -> dict: ...   # spawns content -> {content_id}
```

## Steps
- [ ] **1. Failing test** `tests/api/test_opportunities_api.py`:

```python
from fastapi.testclient import TestClient
from gw_geo.api.app import create_app, Services
from gw_geo.api.deps import Principal, get_principal

class StubOpps:
    def list_for_brand(self, *, tenant_id, brand_id):
        return [{"id": "o1", "title": "absent on Gemini", "rationale": "0% mention",
                 "est_impact": 0.9, "engine": "gemini"}]
    def act(self, *, tenant_id, opportunity_id):
        return {"content_id": "c1"}

def _client(role="editor"):
    app = create_app(Services(opportunities=StubOpps()))
    app.dependency_overrides[get_principal] = lambda: Principal(tenant_id="t1", user_id="u1", role=role)
    return TestClient(app)

def test_list_opportunities_uispec_shape():
    r = _client().get("/brands/b1/opportunities")
    assert r.status_code == 200
    row = r.json()[0]
    assert set(row) == {"id", "title", "rationale", "est_impact", "engine"}   # ui-spec §6

def test_act_spawns_content():
    r = _client().post("/opportunities/o1/act", json={})
    assert r.status_code == 200 and r.json() == {"content_id": "c1"}

def test_act_requires_editor_role():
    r = _client(role="viewer").post("/opportunities/o1/act", json={})
    assert r.status_code == 403
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `routers/opportunities.py`. `GET` returns the ui-spec §6 shape from the injected
  `OpportunityService` (tenant from `Principal`, never from the client). `POST .../act` is guarded by
  `require_role("editor","admin","owner")` and delegates to `service.act(...)` → `{content_id}` (which
  calls the T22 content pipeline). Mount in `create_app`.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(api): /opportunities list + act→content flow`

## Acceptance
- `GET /brands/{id}/opportunities` returns `[{id,title,rationale,est_impact,engine}]`;
  `POST /opportunities/{id}/act` returns `{content_id}` and is RBAC-gated (403 for viewer); tenant is
  server-derived; hermetic.
