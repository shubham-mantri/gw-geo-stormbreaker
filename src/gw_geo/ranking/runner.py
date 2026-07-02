"""Ranking runner (M3-T20, m3-design §2.6): wire the per-engine ranking pipeline end-to-end.

For each requested engine: build the measurement-derived labeled dataset (`ranking.labels.
cited_urls_for` + `ranking.dataset.build_dataset`, T05), train a fresh `EngineRankingModel`
(T11) on it via an **injected** `backend_factory` (so the numeric backend -- a fake in tests,
the real scikit-learn-backed one at runtime via `ranking.model.make_backend` -- never leaks into
this module), persist the trained artifact as a `FeatureModel` row (T02), and compose the
engine's `RankingReport` (`ranking.recommend.build_report`, T12) from the trained model, the
asset's current `FeatureVector`, and that engine's citation-source mix.

Every other input this module needs -- ranking candidates already carrying a computed
`FeatureVector`, the current asset's `FeatureVector` per engine, and the per-engine citation-
source mix -- arrives pre-built (T04's `extract_features` and wherever candidate URLs/content
come from is a caller concern, not this module's; m3-design §2.6 draws the same boundary as the
T05/T11/T12 modules it composes). So `run_ranking` never touches an embedder, a vector store, or
an LLM: `backend_factory` is the only injected client here, mirroring how
`content.guardrails.runner.run_guardrails` carries no scoring logic of its own beyond composing
already-built pieces. Hermetic by construction (TRD §12).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from gw_geo.common.db import FeatureModel
from gw_geo.common.models import FeatureVector, RankingReport, SourceType
from gw_geo.ranking.dataset import build_dataset
from gw_geo.ranking.labels import cited_urls_for
from gw_geo.ranking.model import EngineRankingModel, ModelBackend
from gw_geo.ranking.recommend import build_report


def _in_sample_auc(y_true: list[int], y_score: list[float]) -> float | None:
    """In-sample AUC (rank-sum / Mann-Whitney U form); `None` when undefined.

    Neither T05's dataset assembly nor T11's model carve out a held-out split -- `train` fits on
    every labeled example -- so this is a training-fit diagnostic, not a generalization estimate.
    It is computed in pure Python (no scikit-learn import) so `run_ranking` stays backend-
    agnostic like `ranking.model` itself. Undefined (returns `None`, not `0.0` or a raised error)
    when every example shares one label -- there is no ranking signal to score in that case.
    """
    positives = [score for score, label in zip(y_score, y_true, strict=True) if label == 1]
    negatives = [score for score, label in zip(y_score, y_true, strict=True) if label == 0]
    if not positives or not negatives:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in positives for n in negatives)
    return wins / (len(positives) * len(negatives))


def run_ranking(
    *,
    session: Session,
    tenant_id: str,
    brand_id: str,
    engines: list[str],
    candidates_by_engine: dict[str, list[dict[str, Any]]],
    backend_factory: Callable[[], ModelBackend],
    current_by_engine: dict[str, FeatureVector],
    source_mix_by_engine: dict[str, dict[SourceType, float]],
    id_fn: Callable[[], str] | None = None,
    model_type: str = "gbt",
) -> dict[str, RankingReport]:
    """Train + persist one per-engine ranking model and return its `RankingReport`.

    For each `engine` in `engines`: labels come from `cited_urls_for` (T05 -- `Citation` rows
    already written by measurement) joined onto `candidates_by_engine[engine]` via
    `build_dataset`; a fresh `EngineRankingModel` is trained on the resulting examples using a
    backend from `backend_factory()` (called once per engine, so a stateful/fittable backend is
    never reused across engines); the trained model's `feature_names`/`importances` plus an
    in-sample AUC (`_in_sample_auc`) are persisted as one new `FeatureModel` row; finally
    `ranking.recommend.build_report` composes the report from the trained model,
    `current_by_engine[engine]`, and `source_mix_by_engine[engine]`.

    `id_fn` defaults to a `uuid4`-based factory (inject a deterministic one for tests, per the
    `measurement.discover`/`content.generate` convention). `model_type` is a plain label
    (default `"gbt"`, matching `Settings.ranking_model_type`'s own default) recorded on the
    persisted `FeatureModel` -- this function never reads `Settings` itself, so config
    resolution for a real run is the CLI's job (`gw_geo.cli`'s `rank` subcommand), keeping this
    runner a pure function of its arguments.

    Commits once after every engine has been processed; returns `{engine: RankingReport}` for
    every requested engine, in `engines` order.
    """
    make_id = id_fn if id_fn is not None else (lambda: str(uuid4()))

    reports: dict[str, RankingReport] = {}
    for engine in engines:
        cited_urls = cited_urls_for(session, tenant_id=tenant_id, brand_id=brand_id, engine=engine)
        examples = build_dataset(
            candidates_by_engine[engine],
            cited_urls,
            engine=engine,
            feature_fn=lambda candidate: candidate["features"],
        )

        model = EngineRankingModel(engine, backend_factory())
        model.train(examples)
        factors = model.importances()

        metrics: dict[str, Any] = {
            "n_examples": len(examples),
            "n_cited": sum(1 for example in examples if example.cited),
        }
        auc = _in_sample_auc(
            [int(example.cited) for example in examples],
            [model.predict(example.features) for example in examples],
        )
        if auc is not None:
            metrics["auc"] = auc

        session.add(
            FeatureModel(
                id=make_id(),
                tenant_id=tenant_id,
                brand_id=brand_id,
                engine=engine,
                model_type=model_type,
                feature_names=[factor.name for factor in factors],
                importances=[factor.weight for factor in factors],
                metrics=metrics,
            )
        )

        reports[engine] = build_report(
            model, current_by_engine[engine], source_mix_by_engine[engine]
        )

    session.commit()
    return reports
