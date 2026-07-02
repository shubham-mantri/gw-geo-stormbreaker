"""Tests for training dataset assembly (M3-T05, TRD §8) -- pure label join, no DB.

`build_dataset` turns ranking candidates plus a `cited_urls_for`-shaped set of cited URLs into
`LabeledExample` rows; every case here is a plain function call, hermetic by construction
(TRD §12).
"""

from __future__ import annotations

from typing import Any

from gw_geo.common.models import FeatureVector, LabeledExample
from gw_geo.ranking.dataset import build_dataset


def _fv() -> FeatureVector:
    return FeatureVector(
        structure_score=0.5,
        info_density=3.0,
        freshness_days=5.0,
        domain_authority=0.6,
        corroboration_count=2,
        embedding_similarity=0.7,
        has_schema=True,
        has_faq=False,
        table_count=1,
    )


def test_labels_from_cited_set() -> None:
    cands: list[dict[str, Any]] = [
        {"url": "https://a.com/x", "features": _fv()},
        {"url": "https://b.com/y", "features": _fv()},
    ]
    ds = build_dataset(
        cands, {"https://a.com/x"}, engine="perplexity", feature_fn=lambda c: c["features"]
    )
    assert len(ds) == 2
    by_url = {c["url"]: e for c, e in zip(cands, ds)}
    assert by_url["https://a.com/x"].cited is True
    assert by_url["https://b.com/y"].cited is False
    assert all(e.engine == "perplexity" for e in ds)


def test_empty_candidates_yields_empty_dataset() -> None:
    result = build_dataset([], set(), engine="openai", feature_fn=lambda c: c["features"])
    assert result == []


def test_stamps_requested_engine_and_uses_feature_fn_result() -> None:
    fv = _fv()
    cands: list[dict[str, Any]] = [{"url": "https://a.com/x", "features": fv}]
    ds = build_dataset(cands, set(), engine="gemini", feature_fn=lambda c: c["features"])
    assert len(ds) == 1
    example = ds[0]
    assert isinstance(example, LabeledExample)
    assert example.engine == "gemini"
    assert example.cited is False  # cited_urls is empty
    assert example.features is fv
