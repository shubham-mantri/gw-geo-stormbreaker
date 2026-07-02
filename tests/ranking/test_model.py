"""Tests for the per-engine interpretable ranking model (M3-T11, m3-design §2.3).

`EngineRankingModel` delegates all numeric work to an **injected** `ModelBackend` -- these tests
use a deterministic `FakeBackend` (no scikit-learn import anywhere in this module), so the
hermetic default suite never needs scikit-learn installed. `make_backend`'s real sklearn wrapper
is exercised only by live/manual runs, never by these tests (mirrors `content.kb`'s Pinecone/pgvector
backends).
"""

from __future__ import annotations

from gw_geo.common.models import FeatureVector, LabeledExample
from gw_geo.ranking.model import _UNKNOWN_FRESHNESS_DAYS, FEATURE_NAMES, EngineRankingModel


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


class _CapturingBackend:
    """Records the raw `list[float]` rows it's fit/predicted on (never scores anything) --
    lets a test assert on exactly what `EngineRankingModel` substituted for a `None` feature,
    which the `FeatureVector`-level fakes above can't observe.
    """

    def __init__(self) -> None:
        self.fit_rows: list[list[float]] | None = None
        self.predict_rows: list[list[float]] | None = None

    def fit(self, X: list[list[float]], y: list[int]) -> None:
        self.fit_rows = X

    def predict_proba(self, X: list[list[float]]) -> list[float]:
        self.predict_rows = X
        return [0.5 for _ in X]

    def feature_importances(self) -> list[float]:
        assert self.fit_rows is not None
        return [0.0 for _ in self.fit_rows[0]]


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


def _fv_unknown_freshness(structure: float) -> FeatureVector:
    """Like `_fv`, but `freshness_days=None` -- the "publish date unknown" case."""
    return _fv(structure).model_copy(update={"freshness_days": None})


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


def test_train_predict_with_unknown_freshness_does_not_raise() -> None:
    # `freshness_days=None` (publish date unknown) must not blow up `FeatureVector.as_list`'s
    # `float(None)` cast -- a regression here would TypeError out of both `train` and `predict`.
    m = EngineRankingModel("perplexity", FakeBackend())
    m.train(
        [
            LabeledExample(engine="perplexity", features=_fv(0.9), cited=True),
            LabeledExample(
                engine="perplexity", features=_fv_unknown_freshness(0.1), cited=False
            ),
        ]
    )
    prediction = m.predict(_fv_unknown_freshness(0.5))
    assert 0.0 <= prediction <= 1.0


def test_unknown_freshness_is_substituted_with_stale_sentinel() -> None:
    # Pins the actual substitution, not just "doesn't crash": an unknown publish date must read
    # as very stale (`_UNKNOWN_FRESHNESS_DAYS`), never silently as `0.0`/maximally fresh -- which
    # wouldn't raise a TypeError either, so the no-raise test above can't catch that regression.
    backend = _CapturingBackend()
    m = EngineRankingModel("perplexity", backend)
    freshness_index = FEATURE_NAMES.index("freshness_days")

    m.train([LabeledExample(engine="perplexity", features=_fv_unknown_freshness(0.9), cited=True)])
    assert backend.fit_rows is not None
    assert backend.fit_rows[0][freshness_index] == _UNKNOWN_FRESHNESS_DAYS

    m.predict(_fv_unknown_freshness(0.1))
    assert backend.predict_rows is not None
    assert backend.predict_rows[0][freshness_index] == _UNKNOWN_FRESHNESS_DAYS
