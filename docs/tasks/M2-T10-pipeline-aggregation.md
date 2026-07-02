# M2-T10 — Pipeline aggregation (method breakdown + confidence)

**Depends on:** T06, T07, T08, T09 · **Wave:** 2 (after those land) · **Suggested agent:** general-purpose

**Goal:** The single function the `/pipeline` endpoint calls. Composes all four mechanisms into the
ui-spec §3.6 shape, with the **method breakdown + confidence note front and centre** — the
honesty/anti-overclaim backbone (m2-design §1, PRD §13).

**Files:**
- Create: `src/gw_geo/attribution/pipeline.py`
- Test: `tests/attribution/test_pipeline.py`

## Interface

```python
def pipeline_view(session, *, tenant_id: str, brand_id: str,
                  since: str, until: str,
                  visibility_series: list[dict] | None = None) -> dict:
    """Returns (matches ui-spec §6 GET /brands/{id}/pipeline):
    {
      "influenced": float,        # $ influenced (any mechanism touched the lead)
      "attributed": float,        # $ direct + citation_linked (defensible)
      "leads": int,
      "lift": float,              # holdout incrementality lift_pct (the only causal number)
      "top_answers": [{"prompt": str, "leads": int, "value": float}],
      "method_breakdown": {"direct": float, "citation_linked": float,
                           "assisted": float, "holdout_incremental": float},
      "confidence_note": str      # plain-language honesty disclosure
    }"""
```

## Steps
- [ ] **1. Failing test** `tests/attribution/test_pipeline.py`:

```python
from gw_geo.attribution.pipeline import pipeline_view

def test_pipeline_shape_and_breakdown(seeded_full_attribution):
    # fixture seeds sessions/leads/links across all 4 methods + a holdout cohort for t1/b1
    out = pipeline_view(seeded_full_attribution, tenant_id="t1", brand_id="b1",
                        since="2026-06-01", until="2026-07-02")
    assert set(out) >= {"influenced","attributed","leads","lift",
                        "top_answers","method_breakdown","confidence_note"}
    mb = out["method_breakdown"]
    assert set(mb) == {"direct","citation_linked","assisted","holdout_incremental"}
    # attributed is the defensible subset -> <= influenced
    assert out["attributed"] <= out["influenced"]
    assert out["confidence_note"]                      # never empty
    assert isinstance(out["top_answers"], list)

def test_tenant_isolation(seeded_full_attribution):
    other = pipeline_view(seeded_full_attribution, tenant_id="t2", brand_id="b1",
                          since="2026-06-01", until="2026-07-02")
    assert other["leads"] == 0                          # t2 sees nothing of t1
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** by aggregating `attribution_link` rows (tenant-scoped) grouped by `method`;
  `attributed` = direct+citation_linked $; `influenced` = all methods $ (dedup per lead);
  `lift` = `measure_incrementality`; `top_answers` from citation_linked links grouped by prompt;
  `confidence_note` states holdout is the only causal figure and the rest are correlational.
- [ ] **4. Run → pass**; mypy clean on touched `common`.
- [ ] **5. Commit:** `feat(attribution): pipeline aggregation with method breakdown`

## Acceptance
- Returns the exact ui-spec §3.6/§6 shape; `method_breakdown` has all four keys;
  `attributed <= influenced`; `confidence_note` non-empty and states the causal caveat; tenant-scoped
  (other tenant sees 0); hermetic.
