"""Per-engine interpretable citation model (PRD §6.3, TRD §8, m3-design §2.3).

One `EngineRankingModel` per engine, learning which content features (`ranking/features.py`)
predict citation for that engine specifically -- what earns a citation on Perplexity is not what
earns one on ChatGPT. The numeric backend is **injected** (`ModelBackend` Protocol), exactly like
`content.kb.KnowledgeBase`'s injected `VectorStore`/`EmbeddingClient`: hermetic tests supply a
deterministic fake, and `make_backend` builds the real scikit-learn-backed implementation only at
runtime. `EngineRankingModel` itself never imports scikit-learn -- it only calls the `ModelBackend`
Protocol methods -- so this module is fully testable without the package installed.
"""

from __future__ import annotations

from typing import Any, Protocol

from gw_geo.common.models import FeatureFactor, FeatureVector, LabeledExample

# Sentinel for "freshness unknown" (`FeatureVector.freshness_days is None`): treated as very stale
# (10 years) rather than as missing, so an unknown publish date never reads as "maximally fresh".
_UNKNOWN_FRESHNESS_DAYS = 3650.0


class ModelBackend(Protocol):
    """Anything that can fit a binary classifier and report feature importances.

    Injected into `EngineRankingModel` so tests never need scikit-learn: `make_backend` is the
    only place that builds a real (sklearn) implementation.
    """

    def fit(self, X: list[list[float]], y: list[int]) -> None: ...

    def predict_proba(self, X: list[list[float]]) -> list[float]:  # P(cited) per row
        ...

    def feature_importances(self) -> list[float]: ...


# Order matches `FeatureVector`'s fields (m3-design §2.3); `EngineRankingModel.train`/`predict` map
# a `FeatureVector` to a same-ordered `list[float]` via `FeatureVector.as_list(FEATURE_NAMES)`.
FEATURE_NAMES: list[str] = [
    "structure_score",
    "info_density",
    "freshness_days",
    "domain_authority",
    "corroboration_count",
    "embedding_similarity",
    "has_schema",
    "has_faq",
    "table_count",
]


def _as_row(fv: FeatureVector, feature_names: list[str]) -> list[float]:
    """`FeatureVector.as_list`, with the "unknown freshness" sentinel applied.

    `FeatureVector.as_list` casts `None` (`freshness_days` when the publish date is unknown) to
    `float(None)`, which raises -- so substitute the sentinel on the vector before delegating.
    """
    if fv.freshness_days is None:
        fv = fv.model_copy(update={"freshness_days": _UNKNOWN_FRESHNESS_DAYS})
    return fv.as_list(feature_names)


class EngineRankingModel:
    """A trained-per-engine wrapper around an injected `ModelBackend`.

    All feature-vector <-> raw-row conversion lives here so `ModelBackend` implementations (fake
    or real) only ever see plain `list[float]`/`list[int]` -- they don't need to know about
    `FeatureVector` or `LabeledExample` at all.
    """

    def __init__(
        self,
        engine: str,
        backend: ModelBackend,
        feature_names: list[str] = FEATURE_NAMES,
    ) -> None:
        self._engine = engine
        self._backend = backend
        self._feature_names = feature_names

    @property
    def engine(self) -> str:
        return self._engine

    def train(self, examples: list[LabeledExample]) -> None:
        """Fit the backend on `examples`, mapped to `(X, y)` via `FeatureVector.as_list`."""
        X = [_as_row(example.features, self._feature_names) for example in examples]
        y = [int(example.cited) for example in examples]
        self._backend.fit(X, y)

    def predict(self, fv: FeatureVector) -> float:
        """P(cited) for `fv`, per the trained backend."""
        row = _as_row(fv, self._feature_names)
        return self._backend.predict_proba([row])[0]

    def importances(self) -> list[FeatureFactor]:
        """`FeatureFactor`s for every feature, ranked by `|weight|` descending.

        `direction` comes from the sign of the backend's reported importance: negative for a
        negative logistic-regression coefficient, "positive" for everything else (GBT importances
        are always >= 0, so they are always "positive").
        """
        weights = self._backend.feature_importances()
        factors = [
            FeatureFactor(
                name=name,
                weight=weight,
                direction="negative" if weight < 0 else "positive",
                explanation=(
                    f"{name} is {'negatively' if weight < 0 else 'positively'} associated with "
                    f"citation on {self._engine} (importance {weight:.3f})."
                ),
            )
            for name, weight in zip(self._feature_names, weights, strict=True)
        ]
        factors.sort(key=lambda factor: abs(factor.weight), reverse=True)
        return factors


def make_backend(model_type: str) -> ModelBackend:
    """Build the real scikit-learn-backed `ModelBackend` (m3-design §2.3, config `ranking_model_type`).

    `"gbt"` (default) -> `GradientBoostingClassifier`; `"logreg"` -> `LogisticRegression`.
    `scikit-learn` is a declared project dependency (see `pyproject.toml`) that is **not** installed
    in every environment that merely imports `gw_geo.ranking.model` -- so `sklearn` is imported
    lazily, inside this function, never at module import time. Mirrors `content.kb`'s lazy
    `pinecone` import in `PineconeVectorStore._index`. Not exercised by the hermetic test suite,
    which injects a fake `ModelBackend` directly into `EngineRankingModel`.
    """
    if model_type == "gbt":
        from sklearn.ensemble import GradientBoostingClassifier  # type: ignore[import-not-found]

        return _SklearnBackend(GradientBoostingClassifier())
    if model_type == "logreg":
        from sklearn.linear_model import LogisticRegression  # type: ignore[import-not-found]

        return _SklearnBackend(LogisticRegression())
    raise ValueError(f"unknown ranking_model_type: {model_type!r}")


class _SklearnBackend:
    """Adapts a fitted-in-place scikit-learn classifier to the `ModelBackend` Protocol.

    Only ever constructed by `make_backend`, which is the sole place `sklearn` is imported --
    this class itself has no top-level scikit-learn import, so simply defining it never requires
    the package to be installed (only *calling* `make_backend` does).
    """

    def __init__(self, estimator: Any) -> None:
        self._estimator = estimator

    def fit(self, X: list[list[float]], y: list[int]) -> None:
        self._estimator.fit(X, y)

    def predict_proba(self, X: list[list[float]]) -> list[float]:
        # sklearn's predict_proba returns one [P(class=0), P(class=1)] row per sample; column 1
        # is P(cited) since `y` is fit with `cited` cast to `1`/`0`.
        return [float(row[1]) for row in self._estimator.predict_proba(X)]

    def feature_importances(self) -> list[float]:
        # GradientBoostingClassifier exposes `feature_importances_` (always >= 0); LogisticRegression
        # exposes `coef_` instead (shape `(1, n_features)` for binary classification, signed).
        if hasattr(self._estimator, "feature_importances_"):
            return [float(v) for v in self._estimator.feature_importances_]
        return [float(v) for v in self._estimator.coef_[0]]
