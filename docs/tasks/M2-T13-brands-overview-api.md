# M2-T13 — API: brands + overview

**Depends on:** T04 (+ M1 `measurement/feed.py`) · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** The brand-management + landing-screen endpoints (ui-spec §6, §3.1). Tenant-scoped, RBAC-gated
writes. Shapes are **binding**.

**Files:**
- Create: `src/gw_geo/api/routers/brands.py`
- Edit: `src/gw_geo/api/app.py` (mount router), `src/gw_geo/api/schemas.py`
- Test: `tests/api/test_brands.py`

## Interface (ui-spec §6, verbatim shapes)

```
GET  /brands                       -> 200 [{id,name,domain,competitors:[]}]
POST /brands   {name,domain,competitors?,seed_topics?}   role>=editor  -> 201 {id}
GET  /brands/{id}/overview?range=30d  -> 200
     {sov: float, mention_rate: float, pipeline: float, leads: int,
      trend: [{date, you, competitor}]}      # you vs top competitor SoV series
```
`overview` composes `feed.share_of_voice_trend` (M1 §5) + `attribution.pipeline_view` (T10, for
`pipeline`+`leads`). All reads via `scoped_session`; unknown/foreign brand → 404 (no tenant leak).

## Steps
- [ ] **1. Failing test** `tests/api/test_brands.py`:

```python
def test_list_brands_scoped(app_client, t1_token, seeded_brands):   # t1 owns b1; t2 owns b2
    r = app_client.get("/brands", headers={"Authorization": f"Bearer {t1_token}"})
    assert r.status_code == 200
    ids = [b["id"] for b in r.json()]
    assert "b1" in ids and "b2" not in ids            # tenant isolation

def test_create_brand_requires_editor(app_client, viewer_token):
    r = app_client.post("/brands", json={"name":"Acme","domain":"acme.com"},
                        headers={"Authorization": f"Bearer {viewer_token}"})
    assert r.status_code == 403

def test_overview_shape(app_client, t1_token, seeded_snapshots):
    r = app_client.get("/brands/b1/overview?range=30d",
                       headers={"Authorization": f"Bearer {t1_token}"})
    body = r.json()
    assert set(body) >= {"sov","mention_rate","pipeline","leads","trend"}
    assert isinstance(body["trend"], list)

def test_overview_foreign_brand_404(app_client, t1_token):
    assert app_client.get("/brands/b2/overview",
        headers={"Authorization": f"Bearer {t1_token}"}).status_code == 404
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** the router + Pydantic response schemas; `POST /brands` guarded by
  `require_role("editor")` (optionally kicks off onboarding/prompt discovery async — stub acceptable);
  overview composes feed + pipeline_view. A brand not owned by the token tenant → `LookupError` → 404.
- [ ] **4. Run → pass**; `mypy src/gw_geo/common` clean.
- [ ] **5. Commit:** `feat(api): brands + overview endpoints`

## Acceptance
- `GET /brands` returns only the token tenant's brands; `POST /brands` needs `editor`+; overview
  returns the exact `{sov,mention_rate,pipeline,leads,trend}` shape; foreign brand → 404; hermetic.
