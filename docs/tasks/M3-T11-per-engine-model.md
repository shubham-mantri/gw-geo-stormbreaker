# M3-T11 — Per-engine ranking model (interpretable)

**Depends on:** T04, T05 · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** One **interpretable** model per engine (PRD §6.3, TRD §8) that learns which features predict
citation. The numeric backend is **injected** (`ModelBackend` Protocol) so tests use a deterministic
fake; the real backend wraps scikit-learn **GradientBoostingClassifier** (default) or
**LogisticRegression** (config `ranking_model_type`). Emits interpretable `FeatureFactor`s from
feature importances.

**Files:**
- Create: `src/gw_geo/ranking/model.py`
- Test: `tests/ranking/test_model.py`

## Interface

```python
from typing import Protocol
from gw_geo.common.models import LabeledExample, FeatureVector, FeatureFactor

class ModelBackend(Protocol):
    def fit(self, X: list[list[float]], y: list[int]) -> None: ...
    def predict_proba(self, X: list[list[float]]) -> list[float]: ...   # P(cited) per row
    def feature_importances(self) -> list[float]: ...

FEATURE_NAMES: list[str] = [
    "structure_score", "info_density", "freshness_days", "domain_authority",
    "corroboration_count", "embedding_similarity", "has_schema", "has_faq", "table_count",
]

class EngineRankingModel:
    def __init__(self, engine: str, backend: ModelBackend,
                 feature_names: list[str] = FEATURE_NAMES) -> None: ...
    def train(self, examples: list[LabeledExample]) -> None: ...
    def predict(self, fv: FeatureVector) -> float: ...                  # citation probability
    def importances(self) -> list[FeatureFactor]: ...                   # ranked by |weight| desc

def make_backend(model_type: str) -> ModelBackend: ...    # "gbt"|"logreg" → sklearn wrapper
```

Note: `has_schema`/`has_faq` bools → 1.0/0.0 in the vector (via `FeatureVector.as_list`). `freshness_days`
None → a large sentinel (e.g. 3650.0) so "unknown freshness" is treated as stale.

## Steps
- [ ] **1. Failing test** `tests/ranking/test_model.py` (deterministic fake backend — no sklearn):

```python
from gw_geo.common.models import LabeledExample, FeatureVector
from gw_geo.ranking.model import EngineRankingModel, FEATURE_NAMES

class FakeBackend:
    # "learns" that feature index 0 (structure_score) drives the label
    def __init__(self): self._imp = None
    def fit(self, X, y):
        n = len(X[0]); self._imp = [1.0 if i == 0 else 0.0 for i in range(n)]
    def predict_proba(self, X): return [min(1.0, row[0]) for row in X]
    def feature_importances(self): return self._imp

def _fv(structure):
    return FeatureVector(structure_score=structure, info_density=2.0, freshness_days=5.0,
                         domain_authority=0.5, corroboration_count=1, embedding_similarity=0.5,
                         has_schema=True, has_faq=False, table_count=1)

def test_train_predict_and_importances():
    m = EngineRankingModel("perplexity", FakeBackend())
    m.train([LabeledExample(engine="perplexity", features=_fv(0.9), cited=True),
             LabeledExample(engine="perplexity", features=_fv(0.1), cited=False)])
    assert m.predict(_fv(0.9)) > m.predict(_fv(0.1))
    factors = m.importances()
    assert factors[0].name == "structure_score"          # highest importance ranked first
    assert factors[0].direction == "positive" and factors[0].weight == 1.0

def test_feature_names_match_vector_order():
    assert FEATURE_NAMES[0] == "structure_score" and "embedding_similarity" in FEATURE_NAMES
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `model.py`. `train` maps examples → `(X, y)` via `FeatureVector.as_list(feature_names)`;
  `importances` zips `feature_names` with `backend.feature_importances()`, sorts by `|weight|` desc,
  sets `direction` from sign (for logreg coefficients; GBT importances are ≥0 → "positive"). `make_backend`
  wraps sklearn — **not** exercised by these tests (fake backend injected).
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(ranking): per-engine interpretable citation model`

## Acceptance
- `EngineRankingModel` trains via injected backend, predicts citation probability, and emits
  `FeatureFactor`s ranked by importance; sklearn never called in the default suite.
