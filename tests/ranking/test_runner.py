"""Tests for the ranking runner (M3-T20, m3-design §2.6) -- the per-engine wiring choke point.

Drives `run_ranking` against an in-memory SQLite session (TRD §12) with a seeded `Citation` (the
T05 label source) and the deterministic `FakeBackend` from `tests.ranking.test_model` (no
scikit-learn import anywhere in this module), so the hermetic default suite never needs
scikit-learn installed -- mirrors `tests/ranking/test_model.py` / `test_recommend.py`.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.db import Base, Brand, Citation, FeatureModel, Prompt, Tenant
from gw_geo.common.models import FeatureVector, SourceType
from gw_geo.ranking.runner import run_ranking
from tests.ranking.test_model import FakeBackend


def _session() -> Session:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    # FK parents: the seeded Citation (-> tenant, brand, prompt) and the FeatureModel run_ranking
    # persists (-> tenant, brand).
    s.add(Tenant(id="t1", name="t", sampling_budget_daily=100.0))
    s.add(Brand(id="b1", tenant_id="t1", name="b", domain="b.com"))
    s.add(Prompt(id="p1", tenant_id="t1", brand_id="b1", text="q"))
    s.commit()
    return s


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


def test_run_ranking_trains_persists_and_reports() -> None:
    s = _session()
    s.add(
        Citation(
            id="1",
            tenant_id="t1",
            brand_id="b1",
            url="https://a.com/x",
            domain="a.com",
            source_type="own_site",
            engine="perplexity",
            prompt_id="p1",
        )
    )
    s.commit()

    reports = run_ranking(
        session=s,
        tenant_id="t1",
        brand_id="b1",
        engines=["perplexity"],
        candidates_by_engine={
            "perplexity": [
                {"url": "https://a.com/x", "features": _fv(0.9)},
                {"url": "https://b.com/y", "features": _fv(0.1)},
            ]
        },
        backend_factory=lambda: FakeBackend(),
        current_by_engine={"perplexity": _fv(0.1)},
        source_mix_by_engine={"perplexity": {SourceType.REDDIT: 0.7}},
        id_fn=lambda: "m1",
    )

    assert "perplexity" in reports and reports["perplexity"].factors
    assert s.get(FeatureModel, "m1") is not None  # model artifact persisted


def test_run_ranking_persists_one_model_per_engine() -> None:
    """Acceptance: "trains + persists one `feature_model` per engine" -- exercise two engines.

    `openai` has no seeded `Citation`, so every `openai` candidate is uncited -- this also
    exercises the `_in_sample_auc` "single label, undefined" branch (no `auc` key) alongside
    `perplexity`'s mixed-label branch (which does get an `auc` key), without asserting exact
    score values that would make this test brittle to `FakeBackend`/`_in_sample_auc` internals.
    """
    s = _session()
    s.add(
        Citation(
            id="1",
            tenant_id="t1",
            brand_id="b1",
            url="https://a.com/x",
            domain="a.com",
            source_type="own_site",
            engine="perplexity",
            prompt_id="p1",
        )
    )
    s.commit()

    candidates = [
        {"url": "https://a.com/x", "features": _fv(0.9)},
        {"url": "https://b.com/y", "features": _fv(0.1)},
    ]
    ids = iter(["m1", "m2"])

    reports = run_ranking(
        session=s,
        tenant_id="t1",
        brand_id="b1",
        engines=["perplexity", "openai"],
        candidates_by_engine={"perplexity": candidates, "openai": candidates},
        backend_factory=lambda: FakeBackend(),
        current_by_engine={"perplexity": _fv(0.1), "openai": _fv(0.1)},
        source_mix_by_engine={"perplexity": {SourceType.REDDIT: 0.7}, "openai": {}},
        id_fn=lambda: next(ids),
    )

    assert set(reports) == {"perplexity", "openai"}
    m1 = s.get(FeatureModel, "m1")
    m2 = s.get(FeatureModel, "m2")
    assert m1 is not None and m2 is not None
    assert m1.engine == "perplexity" and m2.engine == "openai"
    assert m1.model_type == "gbt"  # default when the caller doesn't override it
    assert m1.feature_names and len(m1.feature_names) == len(m1.importances)
    assert m1.metrics["n_examples"] == 2 and m1.metrics["n_cited"] == 1
    assert "auc" in m1.metrics  # perplexity has both labels present
    assert m2.metrics["n_cited"] == 0 and "auc" not in m2.metrics  # openai: no citations at all
