# M2-T16 — API: prompts + integrations + lead-capture snippet

**Depends on:** T04, T11, T12, T05 · **Wave:** 3 · **Suggested agent:** general-purpose

**Goal:** The Settings-screen endpoints (ui-spec §6, §3.8): prompt-set CRUD, CRM/GA4 connect, and the
lead-capture install snippet. Tenant-scoped; writes RBAC-gated.

**Files:**
- Create: `src/gw_geo/api/routers/settings.py`
- Edit: `src/gw_geo/api/app.py`, `src/gw_geo/api/schemas.py`
- Test: `tests/api/test_settings_api.py`

## Interface (ui-spec §6, verbatim shapes)

```
GET  /brands/{id}/prompts -> 200 [{id,text,intent_cluster,geo,persona}]
POST /brands/{id}/prompts  {text,intent_cluster?,geo?,persona?}  role>=editor -> 201 {id}
POST /integrations/{kind}  {config}    kind ∈ {hubspot,salesforce,ga4}  role>=admin -> 200 {status}
GET  /lead-capture/snippet?brand_id= -> 200 {snippet}   # <script src=".../gwgeo.js" data-key="...">
```
`/integrations/{kind}` dispatches to the T11/T12 connector's `connect`. `/lead-capture/snippet`
returns the install tag with a per-brand write-key (T05 `resolve_write_key` inverse).

## Steps
- [ ] **1. Failing test** `tests/api/test_settings_api.py`:

```python
def test_prompts_crud_scoped(app_client, t1_token):
    c = app_client.post("/brands/b1/prompts", json={"text":"best CRM for startups","geo":"us"},
                        headers={"Authorization": f"Bearer {t1_token}"})
    assert c.status_code == 201
    r = app_client.get("/brands/b1/prompts", headers={"Authorization": f"Bearer {t1_token}"})
    assert any(p["text"] == "best CRM for startups" for p in r.json())

def test_integration_connect_requires_admin(app_client, editor_token):
    r = app_client.post("/integrations/hubspot", json={"config":{"access_token_ref":"ssm://x"}},
                        headers={"Authorization": f"Bearer {editor_token}"})
    assert r.status_code == 403        # editor < admin

def test_snippet_contains_writekey(app_client, admin_token):
    r = app_client.get("/lead-capture/snippet?brand_id=b1",
                       headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    snip = r.json()["snippet"]
    assert "gwgeo.js" in snip and "data-key=" in snip

def test_unknown_integration_kind_422(app_client, admin_token):
    assert app_client.post("/integrations/bogus", json={"config":{}},
        headers={"Authorization": f"Bearer {admin_token}"}).status_code in (404, 422)
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** prompt CRUD (tenant-scoped, `editor`+ for writes), integration dispatch
  (`admin`+, map kind→connector, `connect` persists the `integration` row), and snippet generation
  (per-brand write-key; script points at the served `gwgeo.js`). Unknown kind → 404/422.
- [ ] **4. Run → pass**; `mypy src/gw_geo/common` clean.
- [ ] **5. Commit:** `feat(api): prompts + integrations + lead-capture snippet endpoints`

## Acceptance
- Prompt CRUD tenant-scoped + `editor`-gated; `POST /integrations/{kind}` `admin`-gated, dispatches to
  the right connector, persists state, rejects unknown kinds; snippet embeds a per-brand write-key;
  hermetic.
