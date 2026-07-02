# M2-T14 — API: visibility + sources

**Depends on:** T04 (+ M1 `measurement/feed.py`) · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** The per-engine deep-dive + citation-source-map endpoints (ui-spec §6, §3.2/§3.3). Every
metric carries its **confidence interval + n_samples** (non-determinism visible, TRD §3). Tenant-scoped.

**Files:**
- Create: `src/gw_geo/api/routers/visibility.py`
- Edit: `src/gw_geo/api/app.py`, `src/gw_geo/api/schemas.py`
- Test: `tests/api/test_visibility.py`

## Interface (ui-spec §6, verbatim shapes)

```
GET /brands/{id}/visibility?range=&geo=&persona= -> 200
    {engines: [{engine, mention_rate, ci: [low,high], cited, avg_position,
                sentiment, n_samples, trend: [{date, mention_rate}]}],
     prompts: [{prompt_id, text, mention_rate, avg_position, n_samples}]}
GET /brands/{id}/sources?range= -> 200
    [{domain, source_type, you_pct, competitor_pcts: {name: pct}}]
```
`visibility` ← `feed.visibility_timeseries`; `sources` ← `feed.citation_source_mix` (M1 §5). CI +
`n_samples` are **required** on every engine row.

## Steps
- [ ] **1. Failing test** `tests/api/test_visibility.py`:

```python
def test_visibility_exposes_confidence(app_client, t1_token, seeded_snapshots):
    r = app_client.get("/brands/b1/visibility?range=30d&geo=us",
                       headers={"Authorization": f"Bearer {t1_token}"})
    assert r.status_code == 200
    eng = r.json()["engines"][0]
    assert {"engine","mention_rate","ci","cited","avg_position","sentiment","n_samples"} <= set(eng)
    assert len(eng["ci"]) == 2 and eng["n_samples"] >= 1     # CI + sample size present

def test_sources_shape(app_client, t1_token, seeded_citations):
    r = app_client.get("/brands/b1/sources?range=30d",
                       headers={"Authorization": f"Bearer {t1_token}"})
    row = r.json()[0]
    assert {"domain","source_type","you_pct","competitor_pcts"} <= set(row)

def test_visibility_tenant_isolation(app_client, t2_token):
    assert app_client.get("/brands/b1/visibility",
        headers={"Authorization": f"Bearer {t2_token}"}).status_code == 404
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** routers + schemas mapping the M1 feed output to the exact shapes; ensure `ci`
  and `n_samples` propagate from `visibility_snapshot`. Foreign brand → 404.
- [ ] **4. Run → pass**; `mypy src/gw_geo/common` clean.
- [ ] **5. Commit:** `feat(api): visibility + sources endpoints`

## Acceptance
- `visibility` returns per-engine rows each with `ci` + `n_samples`; `sources` returns the
  `{domain,source_type,you_pct,competitor_pcts}` shape; both tenant-scoped (foreign brand 404);
  hermetic.
