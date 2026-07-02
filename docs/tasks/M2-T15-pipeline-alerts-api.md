# M2-T15 — API: pipeline + alerts

**Depends on:** T04, T10 · **Wave:** 3 · **Suggested agent:** general-purpose

**Goal:** The payoff endpoints (ui-spec §6, §3.6/§3.7). `/pipeline` is the revenue view — it MUST
return the **method breakdown + confidence note** (anti-overclaim, m2-design §1). `/alerts` surfaces
drift (M1 `drift_event`) + wins. Tenant-scoped.

**Files:**
- Create: `src/gw_geo/api/routers/pipeline.py`
- Edit: `src/gw_geo/api/app.py`, `src/gw_geo/api/schemas.py`
- Test: `tests/api/test_pipeline_api.py`

## Interface (ui-spec §6, verbatim shapes)

```
GET /brands/{id}/pipeline?range= -> 200
    {influenced, attributed, leads, lift,
     top_answers: [{prompt, leads, value}],
     method_breakdown: {direct, citation_linked, assisted, holdout_incremental},
     confidence_note}                         # <- from attribution.pipeline_view (T10), verbatim
GET /brands/{id}/alerts -> 200
    [{severity: "red"|"green"|"yellow", message, ts}]
```
`pipeline` ← `attribution.pipeline_view` (T10). `alerts` ← `drift_event` (M1) mapped to severity +
win detections (e.g. new #1 recommendation).

## Steps
- [ ] **1. Failing test** `tests/api/test_pipeline_api.py`:

```python
def test_pipeline_has_method_breakdown_and_note(app_client, t1_token, seeded_full_attribution):
    r = app_client.get("/brands/b1/pipeline?range=90d",
                       headers={"Authorization": f"Bearer {t1_token}"})
    assert r.status_code == 200
    body = r.json()
    assert set(body["method_breakdown"]) == {"direct","citation_linked","assisted","holdout_incremental"}
    assert body["confidence_note"]                 # honesty rule: never empty
    assert body["attributed"] <= body["influenced"]

def test_alerts_shape(app_client, t1_token, seeded_drift):
    r = app_client.get("/brands/b1/alerts", headers={"Authorization": f"Bearer {t1_token}"})
    a = r.json()[0]
    assert a["severity"] in ("red","green","yellow") and a["message"]

def test_pipeline_tenant_isolation(app_client, t2_token):
    assert app_client.get("/brands/b1/pipeline",
        headers={"Authorization": f"Bearer {t2_token}"}).status_code == 404
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** the routers; `/pipeline` returns `pipeline_view` output unchanged (schema just
  validates the shape); `/alerts` maps `drift_event` rows (drop>threshold → red) + win rules to
  `{severity,message,ts}`. Foreign brand → 404.
- [ ] **4. Run → pass**; `mypy src/gw_geo/common` clean.
- [ ] **5. Commit:** `feat(api): pipeline + alerts endpoints`

## Acceptance
- `/pipeline` returns the full ui-spec §3.6 shape including a non-empty `confidence_note` and all four
  `method_breakdown` keys with `attributed <= influenced`; `/alerts` returns severity-tagged items;
  both tenant-scoped (foreign brand 404); hermetic.
