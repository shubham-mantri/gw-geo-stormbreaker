"""`SourceMap` implementation backing seeding discovery from live measurement data (m4 seeding).

`discovery.discover_targets` reads a citation-source mix through the injected `SourceMap` protocol:
`{"sources": [{"domain", "source_type", "engine", "you_pct", "competitor_pct"}, ...]}`, one row per
`(domain, engine)`. `CitationSourceMap` is the production implementation of that protocol.

**Why this is a DISTINCT class, not `measurement.feed.citation_source_mix`.** The two share a method
name but nothing else. `feed.citation_source_mix` reads the `citation` table and returns a flat
`{source_type: fraction}` volume mix -- it has **no notion of a you-vs-competitor split**, because
`citation` records only that a URL was cited, not whether the answer mentioned the brand or its
competitors. Handing that flat dict to `discover_targets` (which reads `row["sources"]`,
`row["you_pct"]`, `row["competitor_pct"]`) would silently yield **zero** targets. So the you/competitor
split here is a **mention proxy** sourced from `AnswerExtraction` instead: for every cited URL in an
extraction, the answer counts toward "you" if it mentioned the brand (`brand_mentioned`) and toward
"competitor" if it named any competitor (`competitors_present`). It is a proxy -- an answer citing a
domain while mentioning the brand is evidence the engine associates that source with the brand, not
proof the citation itself is about the brand -- but it is the only you-vs-competitor signal the schema
carries, and it is exactly what a citation gap ("competitors are cited here on this source, you are
not") needs.

Tenant/brand scope + window: joins `AnswerExtraction -> ProbeRun -> Prompt`, filters
`ProbeRun.tenant_id` and `Prompt.brand_id`, and bounds `ProbeRun.ts` to the inclusive `[since, until]`
UTC-day range (the same half-open bound convention as `measurement.feed`). No write, no network I/O.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from gw_geo.common.db import AnswerExtraction, Brand, Prompt, ProbeRun
from gw_geo.common.models import SourceType
from gw_geo.measurement.parse import classify_source, domain_of


def _inclusive_date_bounds(since: str, until: str) -> tuple[datetime, datetime]:
    """`[since, until]` inclusive UTC-day bounds as a half-open `(start, end)` datetime range.

    Matches `measurement.feed._inclusive_date_bounds` so a seeding window and a dashboard window
    over the same `[since, until]` cover an identical set of `ProbeRun.ts` values.
    """
    start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    return start, end


def _classify_with_brand(url: str, own_domain: str) -> SourceType:
    """Classify `url`'s source type, tagging the brand's own domain (or subdomains) as OWN_SITE.

    Mirrors `measurement.parse._classify_for_brand` using the public `domain_of`/`classify_source`
    helpers -- `classify_source` is brand-agnostic and never returns OWN_SITE, so the brand-domain
    check is applied here (design S2.1: "own_site via brand.domain").
    """
    domain = domain_of(url)
    if own_domain and (domain == own_domain or domain.endswith(f".{own_domain}")):
        return SourceType.OWN_SITE
    return classify_source(url)


class _Bucket:
    """Mutable per-`(engine, domain)` tally: cited-URL count and you/competitor mention counts."""

    __slots__ = ("source_type", "total", "you", "comp")

    def __init__(self, source_type: SourceType) -> None:
        self.source_type = source_type
        self.total = 0
        self.you = 0
        self.comp = 0


class CitationSourceMap:
    """`SourceMap` (see `seeding.discovery`) computed live from `AnswerExtraction` mention proxies.

    Construct with a plain SQLAlchemy `Session`; `citation_source_mix` is read-only and does no
    network I/O, so it is safe to call at request/job time. See the module docstring for why this
    must not be replaced by `measurement.feed.citation_source_mix`.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def citation_source_mix(
        self, *, tenant_id: str, brand_id: str, since: str, until: str
    ) -> dict[str, Any]:
        """Return the you-vs-competitor citation-source mix for `brand_id` over `[since, until]`.

        One `{"domain", "source_type", "engine", "you_pct", "competitor_pct"}` row per observed
        `(domain, engine)` pair, sorted by `(domain, engine)` for a deterministic result. `you_pct`
        / `competitor_pct` are the fraction of that bucket's cited URLs whose answer mentioned the
        brand / named any competitor, respectively (the mention proxy). Returns `{"sources": []}`
        when nothing matches.
        """
        start, end = _inclusive_date_bounds(since, until)
        brand = self._session.get(Brand, brand_id)
        own_domain = domain_of(brand.domain) if brand and brand.tenant_id == tenant_id else ""

        stmt = (
            select(
                ProbeRun.engine,
                AnswerExtraction.cited_urls,
                AnswerExtraction.brand_mentioned,
                AnswerExtraction.competitors_present,
            )
            .select_from(AnswerExtraction)
            .join(ProbeRun, AnswerExtraction.probe_run_id == ProbeRun.id)
            .join(Prompt, ProbeRun.prompt_id == Prompt.id)
            .where(
                ProbeRun.tenant_id == tenant_id,
                Prompt.brand_id == brand_id,
                ProbeRun.ts >= start,
                ProbeRun.ts < end,
            )
        )

        buckets: dict[tuple[str, str], _Bucket] = {}
        for engine, cited_urls, brand_mentioned, competitors_present in self._session.execute(stmt):
            has_competitor = bool(competitors_present)
            for url in cited_urls or []:
                domain = domain_of(url)
                key = (engine, domain)
                bucket = buckets.get(key)
                if bucket is None:
                    bucket = _Bucket(_classify_with_brand(url, own_domain))
                    buckets[key] = bucket
                bucket.total += 1
                if brand_mentioned:
                    bucket.you += 1
                if has_competitor:
                    bucket.comp += 1

        sources = [
            {
                "domain": domain,
                "source_type": bucket.source_type.value,
                "engine": engine,
                "you_pct": bucket.you / bucket.total,
                "competitor_pct": bucket.comp / bucket.total,
            }
            for (engine, domain), bucket in buckets.items()
        ]
        sources.sort(key=lambda row: (row["domain"], row["engine"]))
        return {"sources": sources}


__all__ = ["CitationSourceMap"]
