"""Training dataset assembly (M3-T05, TRD §8): pure label join over ranking candidates.

`build_dataset` is deliberately DB-free -- it turns a list of ranking candidates (each supplying
whatever `feature_fn` needs to produce a `FeatureVector`, e.g. the output of a feature-extraction
task) plus a `labels.cited_urls_for`-shaped set of cited URLs into `LabeledExample` rows a
per-engine ranking model can train on (TRD §8: "Labels from measurement (cited vs not)"). Keeping
the join pure (no session, no I/O) makes it hermetic and reusable regardless of where
`candidates` or `cited_urls` come from.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from gw_geo.common.models import FeatureVector, LabeledExample


def build_dataset(
    candidates: list[dict[str, Any]],
    cited_urls: set[str],
    *,
    engine: str,
    feature_fn: Callable[[dict[str, Any]], FeatureVector],
) -> list[LabeledExample]:
    """Label each candidate by `candidate["url"]` membership in `cited_urls`.

    Order-preserving 1:1 map from `candidates` to the returned list; every `LabeledExample` is
    stamped with `engine` (a label is inherently per-engine -- a URL cited by one engine says
    nothing about another). `feature_fn` extracts the `FeatureVector` from each candidate dict,
    so this module never needs to know the candidate shape beyond the `"url"` key.
    """
    return [
        LabeledExample(
            engine=engine,
            features=feature_fn(candidate),
            cited=candidate["url"] in cited_urls,
        )
        for candidate in candidates
    ]
