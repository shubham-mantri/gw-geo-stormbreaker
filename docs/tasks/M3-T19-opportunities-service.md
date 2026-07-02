# M3-T19 — Opportunities service (rank gaps)

**Depends on:** T12 · **Wave:** 3 · **Suggested agent:** general-purpose (integration — after Wave 2)

**Goal:** Build the ranked **Opportunities queue** (PRD §6.7, ui-spec §3.4) from the ranking reports +
measurement snapshots. Gap sources: **absence** (low `mention_rate`/`citation_rate` on an engine),
**source** (competitor cited on a `SourceType`/domain the brand is not — from the citation-source mix),
and **sentiment** (neutral/negative on an engine → "add proof/data"). Ranked by `est_impact` desc.
Pure function over measurement + ranking outputs (testable without live services).

**Files:**
- Create: `src/gw_geo/orchestration/opportunities.py`
- Test: `tests/orchestration/test_opportunities.py`, `tests/orchestration/__init__.py`

## Interface

```python
from typing import Any
from gw_geo.common.models import Brand, VisibilitySnapshot, RankingReport, Opportunity

def build_opportunities(*, brand: Brand, snapshots: list[VisibilitySnapshot],
                        reports: list[RankingReport], source_mix: dict[str, Any],
                        id_fn=None) -> list[Opportunity]: ...
# returns Opportunities sorted by est_impact desc, scoped to brand/tenant
```

## Steps
- [ ] **1. Failing test** `tests/orchestration/test_opportunities.py`:

```python
from gw_geo.common.models import (Brand, VisibilitySnapshot, RankingReport,
                                  ChannelRecommendation, SourceType)
from gw_geo.orchestration.opportunities import build_opportunities

BRAND = Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com")

def _snap(engine, mention, sentiment=0.0):
    return VisibilitySnapshot(brand_id="b1", engine=engine, geo="us", persona=None,
                              date="2026-07-02", mention_rate=mention, citation_rate=mention*0.6,
                              avg_position=3.0, sentiment_score=sentiment, share_of_voice=mention,
                              n_samples=10, ci_low=max(0.0, mention-0.1), ci_high=mention+0.1)

def test_absence_generates_ranked_opportunity():
    opps = build_opportunities(
        brand=BRAND,
        snapshots=[_snap("gemini", 0.02), _snap("perplexity", 0.55)],
        reports=[RankingReport(engine="gemini", channel_recommendations=[
            ChannelRecommendation(engine="gemini", channel=SourceType.REDDIT,
                                  rationale="gemini trusts reddit", est_impact=0.9)])],
        source_mix={}, id_fn=lambda: "o1")
    assert opps, "expected at least one opportunity"
    top = opps[0]
    assert top.tenant_id == "t1" and top.brand_id == "b1"
    assert top.engine == "gemini" and top.source_gap in {"absence", "source"}
    # ranked: absence on the low-mention engine outranks the healthy engine
    assert all(opps[i].est_impact >= opps[i+1].est_impact for i in range(len(opps)-1))

def test_sentiment_gap_surfaced():
    opps = build_opportunities(brand=BRAND, snapshots=[_snap("gemini", 0.4, sentiment=0.0)],
                               reports=[], source_mix={}, id_fn=lambda: "o1")
    assert any(o.source_gap == "sentiment" for o in opps)
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `opportunities.py`. Compute `est_impact` per gap (∝ engine weight × gap size),
  build `Opportunity` rows with human-readable `title`/`rationale` (mirroring ui-spec §3.4 copy), sort
  by `est_impact` desc. `id_fn` defaults to uuid4.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(orchestration): opportunities queue from ranking + measurement`

## Acceptance
- Produces tenant/brand-scoped `Opportunity`s for absence/source/sentiment gaps, ranked by
  `est_impact` desc, with actionable titles/rationales; pure/hermetic.
