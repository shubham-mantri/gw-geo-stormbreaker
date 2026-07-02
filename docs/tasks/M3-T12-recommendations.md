# M3-T12 — Recommendations (feature factors + gaps + channel recs)

**Depends on:** T11 · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** Turn a trained model + the current asset's features + the engine's citation-source mix into a
`RankingReport` (PRD §6.3 output): ranked **feature factors**, **content gaps** (a positive factor
whose current value trails a target), and **per-engine channel recommendations** ("Perplexity pulls
from Reddit here → seed Reddit"; channel = `SourceType`).

**Files:**
- Create: `src/gw_geo/ranking/recommend.py`
- Test: `tests/ranking/test_recommend.py`

## Interface

```python
from gw_geo.common.models import (FeatureVector, RankingReport, FeatureFactor,
                                  ContentGap, ChannelRecommendation, SourceType)
from gw_geo.ranking.model import EngineRankingModel

def find_gaps(factors: list[FeatureFactor], current: FeatureVector,
              targets: dict[str, float]) -> list[ContentGap]: ...
def channel_recommendations(engine: str,
                            source_mix: dict[SourceType, float]) -> list[ChannelRecommendation]: ...
def build_report(model: EngineRankingModel, current: FeatureVector,
                 source_mix: dict[SourceType, float],
                 targets: dict[str, float] | None = None) -> RankingReport: ...
```

Rules: a **gap** exists when a positive-direction factor's `current` value < its `target` (default
targets: sensible per-feature bars, e.g. `info_density>=3`, `has_schema>=1`, `corroboration_count>=3`).
Channel recs are the top source-types in `source_mix` (descending weight), rationale names the engine.

## Steps
- [ ] **1. Failing test** `tests/ranking/test_recommend.py`:

```python
from gw_geo.common.models import FeatureVector, FeatureFactor, SourceType
from gw_geo.ranking.recommend import find_gaps, channel_recommendations, build_report
from gw_geo.ranking.model import EngineRankingModel
from tests.ranking.test_model import FakeBackend, _fv, FEATURE_NAMES  # reuse fixtures

def test_find_gaps_flags_below_target():
    factors = [FeatureFactor(name="info_density", weight=0.9, direction="positive", explanation="")]
    cur = _fv(0.5)  # info_density=2.0 in the helper
    gaps = find_gaps(factors, cur, targets={"info_density": 3.0})
    assert len(gaps) == 1 and gaps[0].factor == "info_density"
    assert gaps[0].current_value == 2.0 and gaps[0].target_value == 3.0

def test_channel_recs_ranked_by_mix():
    recs = channel_recommendations("perplexity",
                                   {SourceType.REDDIT: 0.7, SourceType.REVIEW_SITE: 0.2})
    assert recs[0].channel == SourceType.REDDIT and recs[0].engine == "perplexity"
    assert recs[0].est_impact >= recs[1].est_impact

def test_build_report_assembles_all_three():
    m = EngineRankingModel("perplexity", FakeBackend())
    from gw_geo.common.models import LabeledExample
    m.train([LabeledExample(engine="perplexity", features=_fv(0.9), cited=True),
             LabeledExample(engine="perplexity", features=_fv(0.1), cited=False)])
    rep = build_report(m, _fv(0.1), {SourceType.REDDIT: 0.6},
                       targets={"structure_score": 0.8})
    assert rep.engine == "perplexity"
    assert rep.factors and rep.channel_recommendations
    assert any(g.factor == "structure_score" for g in rep.gaps)
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `recommend.py`. `find_gaps` reads current values via `getattr`/`as_list`;
  `build_report` composes `model.importances()` + `find_gaps` + `channel_recommendations`.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(ranking): recommendations — factors, gaps, channel recs`

## Acceptance
- `build_report` returns a populated `RankingReport`; gaps reflect below-target positive factors;
  channel recs are ranked by source mix and name the engine.
