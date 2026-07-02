"""Opportunities queue: rank absence/source/sentiment gaps into `Opportunity` rows.

PRD §6.7 "Opportunity queue: ranked gaps -> one-click spawn generation/seeding tasks", ui-spec
§3.4 ("Opportunities (ranked by est. impact)"), `docs/m3-design.md` §4. `build_opportunities` is a
**pure function** over already-loaded measurement + ranking outputs (`VisibilitySnapshot`,
`RankingReport`) -- no DB/HTTP/LLM calls, so it is fully hermetic (TRD §12). It never mutates its
inputs (m3-design §6: "consumes measurement, never mutates it").

Three gap sources, each independently detected and scored (m3-design §4):

- **absence** -- `VisibilitySnapshot.mention_rate` below `ABSENCE_MENTION_RATE_MAX` on an engine:
  the brand is largely missing from that engine's answers (ui-spec: "you're absent").
- **sentiment** -- `VisibilitySnapshot.sentiment_score` (mapped to `[-1, 1]` by
  `measurement.aggregate.aggregate`, `0.0` = neutral) at or below `SENTIMENT_GAP_SCORE_MAX`: not
  solidly positive, so proof points would help (ui-spec: "Sentiment neutral on Gemini -- add
  proof/data").
- **source** -- a `RankingReport.channel_recommendations` entry (an engine's citation-source mix,
  see `ranking.recommend.channel_recommendations`) naming a channel the brand's own citation mix
  (`source_mix`, e.g. `measurement.feed.citation_source_mix`) barely touches: the engine trusts a
  channel the brand hasn't seeded.

Every `Opportunity.est_impact` is `engine weight x gap size` (m3-design §4): `gap size` is
specific to the gap source (see the `_*_opportunity` builders below); `engine weight` is a
`[0, 1]` confidence multiplier derived from how many samples back the engine's numbers
(`_engine_weight`), so a gap measured off a handful of probes can't out-rank the same gap measured
off a well-sampled engine. The returned list is sorted by `est_impact` descending.

Every `Opportunity` is stamped `tenant_id=brand.tenant_id, brand_id=brand.id` (never trusted from
the input rows -- `VisibilitySnapshot` carries no `tenant_id` at all, and `RankingReport` carries
no brand identity, m3-design §4); `snapshots` not belonging to `brand` are dropped defensively
before scoring.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from gw_geo.common.models import (
    Brand,
    ChannelRecommendation,
    Opportunity,
    RankingReport,
    VisibilitySnapshot,
)

# --------------------------------------------------------------------------------------------
# Gap thresholds
# --------------------------------------------------------------------------------------------

# An engine mentioning/citing the brand less than this fraction of the time counts as "largely
# absent" (ui-spec §3.4), not merely middling.
ABSENCE_MENTION_RATE_MAX = 0.2

# `sentiment_score` (`[-1, 1]`, `0.0` = neutral) at or below this is "not solidly positive" --
# neutral or negative -- and needs proof/data (ui-spec §3.4).
SENTIMENT_GAP_SCORE_MAX = 0.2

# The brand's own share of citation volume on a channel (`source_mix`) at or above this fraction
# means the brand already has a meaningful footprint there -- not a gap.
SOURCE_GAP_PRESENCE_MAX = 0.05

# `n_samples` (summed across a brand's snapshots for one engine) at/above this is "fully
# confident" (`_engine_weight` saturates to 1.0); fewer samples scale the weight down linearly.
_FULL_CONFIDENCE_SAMPLES = 10.0


# --------------------------------------------------------------------------------------------
# Engine confidence weight
# --------------------------------------------------------------------------------------------


def _engine_sample_totals(snapshots: list[VisibilitySnapshot]) -> dict[str, int]:
    """Total `n_samples` per `engine`, across every (geo, persona) snapshot for that engine."""
    totals: dict[str, int] = {}
    for snapshot in snapshots:
        totals[snapshot.engine] = totals.get(snapshot.engine, 0) + snapshot.n_samples
    return totals


def _engine_weight(n_samples: int | None) -> float:
    """Confidence multiplier in `[0, 1]` for an engine's measurement (m3-design §4).

    Stands in for "how much this engine's numbers should move the ranking": a gap measured off a
    handful of probes is real but less trustworthy than the same gap measured off a well-sampled
    engine, so the weight scales linearly up to `_FULL_CONFIDENCE_SAMPLES` samples. `None`/`0`
    (no measured samples for this engine at all -- e.g. a `RankingReport` for an engine that has
    no matching snapshot yet) is treated as "no information either way", not "confidently zero",
    so it defaults to full weight rather than suppressing the gap entirely.
    """
    if not n_samples:
        return 1.0
    return max(0.0, min(1.0, n_samples / _FULL_CONFIDENCE_SAMPLES))


# --------------------------------------------------------------------------------------------
# Per-gap-source Opportunity builders
# --------------------------------------------------------------------------------------------


def _absence_opportunity(
    brand: Brand, snapshot: VisibilitySnapshot, weight: float, make_id: Callable[[], str]
) -> Opportunity | None:
    """An absence gap: `snapshot.mention_rate` below `ABSENCE_MENTION_RATE_MAX`.

    Gap size is `1.0 - mention_rate` -- the more absent the brand is, the more headroom there is
    to gain by closing the gap.
    """
    if snapshot.mention_rate >= ABSENCE_MENTION_RATE_MAX:
        return None
    gap_size = 1.0 - snapshot.mention_rate
    engine = snapshot.engine
    return Opportunity(
        id=make_id(),
        tenant_id=brand.tenant_id,
        brand_id=brand.id,
        title=f"You're largely absent on {engine}",
        rationale=(
            f"{brand.name} is mentioned in only {snapshot.mention_rate:.0%} of {engine} "
            f"answers (n={snapshot.n_samples}), well below a healthy "
            f"{ABSENCE_MENTION_RATE_MAX:.0%}+ presence. Publish extraction-friendly, "
            f"well-cited content targeted at {engine} to close the gap."
        ),
        engine=engine,
        est_impact=weight * gap_size,
        source_gap="absence",
    )


def _sentiment_opportunity(
    brand: Brand, snapshot: VisibilitySnapshot, weight: float, make_id: Callable[[], str]
) -> Opportunity | None:
    """A sentiment gap: `snapshot.sentiment_score` at or below `SENTIMENT_GAP_SCORE_MAX`.

    Gap size maps the `[-1, 1]` sentiment scale to a `[0, 1]` severity: fully negative (`-1.0`)
    is the maximum-size gap, neutral (`0.0`) is a mid-size one, and anything above the threshold
    never reaches this function at all.
    """
    if snapshot.sentiment_score > SENTIMENT_GAP_SCORE_MAX:
        return None
    polarity = "negative" if snapshot.sentiment_score < 0 else "neutral"
    gap_size = (1.0 - snapshot.sentiment_score) / 2.0
    engine = snapshot.engine
    return Opportunity(
        id=make_id(),
        tenant_id=brand.tenant_id,
        brand_id=brand.id,
        title=f"Sentiment {polarity} on {engine} -- add proof/data",
        rationale=(
            f"{brand.name}'s mentions on {engine} skew {polarity} (sentiment score "
            f"{snapshot.sentiment_score:.2f}). Ground upcoming content in concrete proof "
            f"points -- data, certifications, case studies -- to shift perception."
        ),
        engine=engine,
        est_impact=weight * gap_size,
        source_gap="sentiment",
    )


def _source_opportunity(
    brand: Brand,
    channel_rec: ChannelRecommendation,
    source_mix: dict[str, Any],
    weight: float,
    make_id: Callable[[], str],
) -> Opportunity | None:
    """A source gap: `channel_rec`'s channel is barely represented in the brand's `source_mix`.

    `channel_rec.est_impact` is the engine's reliance on the channel (its share of that engine's
    citation mix, see `ranking.recommend.channel_recommendations`); `brand_presence` is the
    brand's own share of citation volume on that same channel, from `source_mix` (e.g.
    `measurement.feed.citation_source_mix`, keyed by `SourceType.value`). Gap size is the
    engine's reliance scaled by how much of that channel the brand is missing -- a channel the
    engine barely uses is a low-impact gap even at zero brand presence.
    """
    if channel_rec.est_impact <= 0:
        return None
    brand_presence = float(source_mix.get(channel_rec.channel.value, 0.0))
    if brand_presence >= SOURCE_GAP_PRESENCE_MAX:
        return None
    engine = channel_rec.engine
    channel_label = channel_rec.channel.value.replace("_", " ")
    gap_size = channel_rec.est_impact * (1.0 - brand_presence)
    return Opportunity(
        id=make_id(),
        tenant_id=brand.tenant_id,
        brand_id=brand.id,
        title=f"{engine} leans on {channel_label} -- you're barely there",
        rationale=(
            f"{brand.name} draws only {brand_presence:.0%} of its own citation volume from "
            f"{channel_label}. {channel_rec.rationale}"
        ),
        engine=engine,
        est_impact=weight * gap_size,
        source_gap="source",
    )


# --------------------------------------------------------------------------------------------
# build_opportunities
# --------------------------------------------------------------------------------------------


def build_opportunities(
    *,
    brand: Brand,
    snapshots: list[VisibilitySnapshot],
    reports: list[RankingReport],
    source_mix: dict[str, Any],
    id_fn: Callable[[], str] | None = None,
) -> list[Opportunity]:
    """Rank absence/sentiment/source gaps from `snapshots` + `reports` into `Opportunity` rows.

    `snapshots` is filtered to `brand.id` first (defensive -- `VisibilitySnapshot` carries no
    `tenant_id` to check); `reports` has no brand identity to filter on at all, so every report's
    channel recommendations are scored as given (the caller is expected to have already scoped
    `reports` to `brand`, mirroring every other consumer of ranking output). `id_fn` defaults to
    a `uuid4`-based factory; inject a deterministic one for tests. Returns every detected
    opportunity, sorted by `est_impact` descending.
    """
    make_id = id_fn if id_fn is not None else (lambda: str(uuid4()))
    brand_snapshots = [snapshot for snapshot in snapshots if snapshot.brand_id == brand.id]
    engine_samples = _engine_sample_totals(brand_snapshots)

    opportunities: list[Opportunity] = []

    for snapshot in brand_snapshots:
        weight = _engine_weight(engine_samples.get(snapshot.engine))
        absence = _absence_opportunity(brand, snapshot, weight, make_id)
        if absence is not None:
            opportunities.append(absence)
        sentiment = _sentiment_opportunity(brand, snapshot, weight, make_id)
        if sentiment is not None:
            opportunities.append(sentiment)

    for report in reports:
        weight = _engine_weight(engine_samples.get(report.engine))
        for channel_rec in report.channel_recommendations:
            source = _source_opportunity(brand, channel_rec, source_mix, weight, make_id)
            if source is not None:
                opportunities.append(source)

    opportunities.sort(key=lambda opportunity: opportunity.est_impact, reverse=True)
    return opportunities
