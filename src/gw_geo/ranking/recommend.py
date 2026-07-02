"""Recommendations: factors + gaps + channel recs -> `RankingReport` (PRD §6.3, m3-design §2.4).

Turns a trained per-engine `EngineRankingModel` (`ranking/model.py`, M3-T11) + the current asset's
`FeatureVector` + the engine's citation-source mix (e.g. `measurement.feed.citation_source_mix`)
into the `RankingReport` the PRD §6.3 output surfaces to the dashboard: ranked **factors**
(`EngineRankingModel.importances()`), **content gaps** (a positive-direction factor whose current
value trails a sensible target), and **channel recommendations** ("Perplexity pulls from Reddit
here -> seed Reddit").

`find_gaps` and `channel_recommendations` are small pure functions over already-computed inputs;
`build_report` is the composer that owns the model and stitches its `importances()` together with
both of them into one report.
"""

from __future__ import annotations

from gw_geo.common.models import (
    ChannelRecommendation,
    ContentGap,
    FeatureFactor,
    FeatureVector,
    RankingReport,
    SourceType,
)
from gw_geo.ranking.model import EngineRankingModel

# Sensible per-feature minimum bars (m3-design §2.4 / task examples: `info_density>=3`,
# `has_schema>=1`, `corroboration_count>=3`), used by `build_report` whenever the caller doesn't
# supply its own `targets`. `freshness_days` is deliberately absent: it is a *lower-is-better*
# feature (fewer days since publish = fresher), so a "current below target is a gap" minimum-bar
# check is the wrong shape of rule for it -- there is no universal "at least this many days
# stale" floor to recommend toward.
DEFAULT_TARGETS: dict[str, float] = {
    "structure_score": 0.75,
    "info_density": 3.0,
    "domain_authority": 0.7,
    "corroboration_count": 3.0,
    "embedding_similarity": 0.7,
    "has_schema": 1.0,
    "has_faq": 1.0,
    "table_count": 1.0,
}


def find_gaps(
    factors: list[FeatureFactor], current: FeatureVector, targets: dict[str, float]
) -> list[ContentGap]:
    """Flag every positive-direction `factor` whose `current` value trails its `targets` bar.

    A negative-direction factor is skipped: "less of this predicts citation" has no sensible
    "raise it to a target" gap. A factor absent from `targets` is skipped too -- no bar, no gap.
    Order follows `factors` (typically `EngineRankingModel.importances()`'s ranked order), so the
    highest-impact gaps come first.

    `ContentGap.engine` is left `""` here -- `find_gaps` is never given an engine (per the TRD
    interface); `build_report` is the composer that knows `model.engine` and stamps it on.
    """
    gaps: list[ContentGap] = []
    for factor in factors:
        if factor.direction != "positive":
            continue
        target = targets.get(factor.name)
        if target is None:
            continue
        raw_value = getattr(current, factor.name, None)
        if raw_value is None:
            continue  # e.g. `freshness_days` unknown -- nothing to compare against a target.
        current_value = float(raw_value)
        if current_value < target:
            gaps.append(
                ContentGap(
                    engine="",
                    factor=factor.name,
                    current_value=current_value,
                    target_value=target,
                )
            )
    return gaps


def channel_recommendations(
    engine: str, source_mix: dict[SourceType, float]
) -> list[ChannelRecommendation]:
    """Rank `source_mix`'s source types by descending weight into per-`engine` channel recs.

    `est_impact` is the source's raw mix weight, so it is monotone with weight by construction;
    the rationale names both the engine and the channel (e.g. "perplexity pulls ~70% of its
    citations from reddit here -- seed/strengthen presence on reddit to capture more of them.").
    """
    ranked = sorted(source_mix.items(), key=lambda item: item[1], reverse=True)
    return [
        ChannelRecommendation(
            engine=engine,
            channel=source_type,
            rationale=(
                f"{engine} pulls ~{weight:.0%} of its citations from {source_type.value} here "
                f"-- seed/strengthen presence on {source_type.value} to capture more of them."
            ),
            est_impact=weight,
        )
        for source_type, weight in ranked
    ]


def build_report(
    model: EngineRankingModel,
    current: FeatureVector,
    source_mix: dict[SourceType, float],
    targets: dict[str, float] | None = None,
) -> RankingReport:
    """Compose `model.importances()` + `find_gaps` + `channel_recommendations` into a report.

    `targets` defaults to `DEFAULT_TARGETS` when omitted (an explicit `targets` dict replaces the
    defaults outright rather than merging with them). `find_gaps`'s gaps carry a blank `engine`
    (see `find_gaps`) -- this is the one place that knows `model.engine`, so every gap is restamped
    with it before the report is returned.
    """
    factors = model.importances()
    resolved_targets = DEFAULT_TARGETS if targets is None else targets
    gaps = [
        gap.model_copy(update={"engine": model.engine})
        for gap in find_gaps(factors, current, resolved_targets)
    ]
    recs = channel_recommendations(model.engine, source_mix)
    return RankingReport(
        engine=model.engine,
        factors=factors,
        gaps=gaps,
        channel_recommendations=recs,
    )
