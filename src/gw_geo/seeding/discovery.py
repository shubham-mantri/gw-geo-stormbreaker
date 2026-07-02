"""Seeding target discovery: citation-source map -> ranked `SeedingTarget`s (m4-design.md S2.1).

Turns the M1 citation-source map (`measurement/feed.citation_source_mix`, injected here as a
`SourceMap` protocol so this module builds and tests before M1 lands, with no live database) into
a ranked list of `SeedingTarget`s -- the high-authority off-site domains/communities an engine
already trusts for the brand's prompts, where a competitor is cited but the brand is not.

`SourceMap.citation_source_mix` returns `{"sources": [{"domain", "source_type", "engine",
"you_pct", "competitor_pct"}, ...]}` -- one row per (domain, engine) pair observed in
`[since, until]`. For each row `discover_targets`:

1. Maps `source_type` to an **active** channel via the injected `ChannelCatalog` (T04); a row
   whose `source_type` has no active channel is dropped outright -- there is nowhere to seed it.
   When more than one active channel shares a `source_type` (e.g. `g2`/`capterra` both map to
   `review_site`), the first one in catalog order wins -- picking a *specific* domain-to-channel
   match is out of scope here (design §2.1 only asks for "an active channel").
2. Computes `gap_score = max(competitor_pct - you_pct, 0.0)`: how much more engines cite
   competitors than the brand on that source. A zero (or negative -- the brand already ties or
   leads) gap is dropped -- there is no gap left to seed against.
3. Computes `priority = gap_score * <source-authority weight>`, a simple per-`source_type`
   constant (`_SOURCE_AUTHORITY_WEIGHT`) approximating how much engines tend to trust that class
   of source, so a big gap on a high-authority source (e.g. Wikipedia) outranks a same-size gap
   on a lower-authority one.

Surviving targets are sorted by `priority` descending and truncated to `limit`. `SeedingTarget` is
a pure data contract consumed downstream by the per-channel brief writer (T06) and the scheduler
(T15) -- its field names/types are load-bearing and must not change without updating both.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from gw_geo.common.models import SourceType
from gw_geo.seeding.channels import Channel, ChannelCatalog

# Simple, versioned per-source-type authority constant (m4-design.md S2.1: "gap_score * source
# authority weight"). Approximates how much engines tend to trust/cite each class of source in an
# answer, independent of any one brand's gap on it -- Wikipedia and press/news outrank a forum
# reply. Only source types with a channel in `ChannelCatalog.default()` can ever reach this
# weighting (an unmapped source type is dropped before this lookup runs), but every `SourceType`
# has an entry so a future channel added for a not-yet-weighted source type degrades to
# `_DEFAULT_AUTHORITY_WEIGHT` instead of raising.
_SOURCE_AUTHORITY_WEIGHT: dict[SourceType, float] = {
    SourceType.WIKIPEDIA: 1.5,
    SourceType.NEWS_PR: 1.4,
    SourceType.REVIEW_SITE: 1.2,
    SourceType.LISTICLE: 1.1,
    SourceType.REDDIT: 1.0,
    SourceType.FORUM_QA: 0.9,
    SourceType.DOCS: 0.8,
    SourceType.SOCIAL: 0.7,
    SourceType.OWN_SITE: 0.5,
    SourceType.OTHER: 0.5,
}
_DEFAULT_AUTHORITY_WEIGHT = 1.0


class SeedingTarget(BaseModel):
    """One ranked off-site seeding opportunity (m4-design.md S2.1).

    Consumed by `seeding/briefs.py` (T06) and the scheduler (T15) -- keep this shape exactly per
    the TRD/design interface.
    """

    channel: str  # seeding_channel.name
    source_type: SourceType
    domain: str
    engine: str
    gap_score: float  # max(competitor_cited_pct - you_cited_pct, 0)
    priority: float  # gap_score * source-authority weight
    rationale: str


class SourceMap(Protocol):
    """Citation-source-mix reader; satisfied in production by `measurement/feed` (M1)."""

    def citation_source_mix(
        self, *, tenant_id: str, brand_id: str, since: str, until: str
    ) -> dict[str, Any]: ...


def _active_channel_by_source_type(channels: ChannelCatalog) -> dict[SourceType, Channel]:
    """First active channel (catalog order) covering each `SourceType`.

    Some source types map to more than one channel (`review_site` -> `g2`/`capterra`, `news_pr`
    -> `pr_wire`/`expert_byline`); `discover_targets` only needs *an* active channel for a given
    source type (design §2.1), so ties resolve deterministically to the first match in catalog
    order via `dict.setdefault`.
    """
    by_source_type: dict[SourceType, Channel] = {}
    for channel in channels.active():
        by_source_type.setdefault(channel.source_type, channel)
    return by_source_type


def discover_targets(
    source_map: SourceMap,
    *,
    tenant_id: str,
    brand_id: str,
    since: str,
    until: str,
    channels: ChannelCatalog,
    limit: int = 25,
) -> list[SeedingTarget]:
    """Rank `source_map`'s citation-source mix into seeding targets (m4-design.md S2.1).

    Drops any row whose `source_type` has no active channel in `channels`, and any row whose
    `gap_score` is not positive (the brand already matches or leads competitors there).
    Survivors are sorted by `priority` descending and truncated to `limit`.
    """
    channel_by_source_type = _active_channel_by_source_type(channels)
    mix = source_map.citation_source_mix(
        tenant_id=tenant_id, brand_id=brand_id, since=since, until=until
    )

    targets: list[SeedingTarget] = []
    for row in mix.get("sources", []):
        source_type = SourceType(row["source_type"])
        channel = channel_by_source_type.get(source_type)
        if channel is None:
            continue  # no active channel seeds this source type

        gap_score = max(row["competitor_pct"] - row["you_pct"], 0.0)
        if gap_score <= 0:
            continue  # brand already ties or leads competitors here -- no gap to seed

        weight = _SOURCE_AUTHORITY_WEIGHT.get(source_type, _DEFAULT_AUTHORITY_WEIGHT)
        domain = row["domain"]
        engine = row["engine"]
        targets.append(
            SeedingTarget(
                channel=channel.name,
                source_type=source_type,
                domain=domain,
                engine=engine,
                gap_score=gap_score,
                priority=gap_score * weight,
                rationale=(
                    f"On {domain} ({channel.name}), {engine} cites competitors "
                    f"{row['competitor_pct']:.0%} of the time vs. your {row['you_pct']:.0%} -- "
                    f"a {gap_score:.0%} citation gap on a channel gw-geo can seed."
                ),
            )
        )

    targets.sort(key=lambda target: target.priority, reverse=True)
    return targets[:limit]
