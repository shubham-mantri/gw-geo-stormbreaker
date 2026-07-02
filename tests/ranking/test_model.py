"""Tests for the per-engine interpretable ranking model (M3-T11, m3-design §2.3).

`EngineRankingModel` delegates all numeric work to an **injected** `ModelBackend` -- these tests
use a deterministic `FakeBackend` (no scikit-learn import anywhere in this module), so the
hermetic default suite never needs scikit-learn installed. `make_backend`'s real sklearn wrapper
is exercised only by live/manual runs, never by these tests (mirrors `content.kb`'s Pinecone/pgvector
backends).
"""

from __future__ import annotations

from gw_geo.common.models import FeatureVector, LabeledExample
from gw_geo.ranking.model import FEATURE_NAMES, EngineRankingModel


class FakeBackend:
    """Deterministic fake: "learns" that feature index 0 (`structure_score`) drives the label."""

    def __init__(self) -> None:
        self._imp: list[float] | None = None

    def fit(self, X: list[list[float]], y: list[int]) -> None:
        n = len(X[0])
        self._imp = [1.0 if i == 0 else 0.0 for i in range(n)]

    def predict_proba(self, X: list[list[float]]) -> list[float]:
        return [min(1.0, row[0]) for row in X]

    def feature_importances(self) -> list[float]:
        assert self._imp is not None
        return self._imp


def _fv(structure: float) -> FeatureVector:
    return FeatureVector(
        structure_score=structure,
        info_density=2.0,
        freshness_days=5.0,
        domain_authority=0.5,
        corroboration_count=1,
        embedding_similarity=0.5,
        has_schema=True,
        has_faq=False,
        table_count=1,
    )


def test_train_predict_and_importances() -> None:
    m = EngineRankingModel("perplexity", FakeBackend())
    m.train(
        [
            LabeledExample(engine="perplexity", features=_fv(0.9), cited=True),
            LabeledExample(engine="perplexity", features=_fv(0.1), cited=False),
        ]
    )
    assert m.predict(_fv(0.9)) > m.predict(_fv(0.1))
    factors = m.importances()
    assert factors[0].name == "structure_score"  # highest importance ranked first
    assert factors[0].direction == "positive" and factors[0].weight == 1.0


def test_feature_names_match_vector_order() -> None:
    assert FEATURE_NAMES[0] == "structure_score" and "embedding_similarity" in FEATURE_NAMES
