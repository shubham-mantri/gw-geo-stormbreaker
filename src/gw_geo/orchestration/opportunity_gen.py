"""Opportunity-generation worker (W3): run :func:`build_opportunities` over a brand's *live*
visibility data and persist the ranked ``Opportunity`` rows the queue serves.

This is the stateful producer the pure gap-ranking function
(:func:`gw_geo.orchestration.opportunities.build_opportunities`) and the read/act service
(:class:`gw_geo.orchestration.opportunity_service.DbOpportunityService`) were both waiting on: the
service can only surface/act on `Opportunity` rows *something* has written, and nothing ran the
ranker before. :func:`generate_and_persist_opportunities` loads the brand's `VisibilitySnapshot`
rows + citation source mix from the DB, ranks the gaps, and writes the result -- so
``GET /brands/{id}/opportunities`` finally returns real recommendations.

Scope (W3): **absence + sentiment** opportunities are derived from snapshots alone. **source**
opportunities need ranking ``RankingReport``s, which require the candidate-sourcing crawler that is
not built yet, so ``reports=[]`` is passed here (no source opportunities this wave). The citation
source mix is still computed and passed so the worker is correct the moment reports are wired in.

Idempotent refresh: re-running replaces the brand's **open** queue (delete the prior ``status=open``
rows, insert the freshly-ranked set), leaving ``acted``/``dismissed`` rows untouched -- so a refresh
never resurrects a dismissed gap, duplicates the queue, or destroys the audit trail an earlier
``act()`` wrote.

FK-safety (real Postgres enforces FKs; SQLite defaults them off -- see the recent measurement-runner
fix): the only rows written here are leaf ``Opportunity`` rows whose ``tenant_id``/``brand_id`` FK
parents (``tenant``/``brand``) are loaded from the *already-committed* DB before any insert. The
worker never creates a parent alongside its child, so there is no intra-transaction flush ordering
to get wrong; a missing/cross-tenant brand short-circuits to ``0`` rather than inserting an orphan.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm import Session as SASession

from gw_geo.common import db, models
from gw_geo.common.config import get_settings
from gw_geo.orchestration.opportunities import build_opportunities

logger = logging.getLogger(__name__)


def _brand_source_mix(
    session: SASession, *, tenant_id: str, brand_id: str
) -> dict[str, float]:
    """`{source_type: fraction}` of the brand's citation volume, `seen_count`-weighted.

    Mirrors :func:`gw_geo.measurement.feed.citation_source_mix`'s weighting (a heavily-repeated
    citation counts more than a one-off), but over **all** of the brand's citations rather than a
    date window -- opportunity generation ranks the brand's standing citation footprint, not one
    reporting period. Returns ``{}`` when the brand has no citations (nothing to divide by).
    """
    counts: dict[str, int] = defaultdict(int)
    rows = (
        session.query(db.Citation)
        .filter(db.Citation.tenant_id == tenant_id, db.Citation.brand_id == brand_id)
        .all()
    )
    for row in rows:
        counts[row.source_type] += row.seen_count
    total = sum(counts.values())
    if not total:
        return {}
    return {source_type: count / total for source_type, count in counts.items()}


def generate_and_persist_opportunities(
    *, session: SASession, tenant_id: str, brand_id: str
) -> int:
    """Rank `brand_id`'s live visibility gaps and persist them as `Opportunity` rows; return count.

    Loads the brand + its `VisibilitySnapshot` rows and citation source mix, runs
    :func:`build_opportunities` (``reports=[]`` -- source opportunities are out of scope this wave),
    then **idempotently refreshes** the open queue: deletes the brand's prior ``status=open``
    opportunities and inserts the freshly-ranked set (``status=open``), preserving any
    ``acted``/``dismissed`` history. A missing or cross-tenant brand is a no-op returning ``0``.
    """
    brand_row = session.get(db.Brand, brand_id)
    if brand_row is None or brand_row.tenant_id != tenant_id:
        logger.warning(
            "brand_id=%r not found for tenant_id=%r; no opportunities generated",
            brand_id,
            tenant_id,
        )
        return 0

    brand = models.Brand(
        id=brand_row.id,
        tenant_id=brand_row.tenant_id,
        name=brand_row.name,
        domain=brand_row.domain,
        competitors=list(brand_row.competitors),
    )

    snapshot_rows = (
        session.query(db.VisibilitySnapshot)
        .filter(
            db.VisibilitySnapshot.tenant_id == tenant_id,
            db.VisibilitySnapshot.brand_id == brand_id,
        )
        .all()
    )
    snapshots = [
        models.VisibilitySnapshot(
            brand_id=row.brand_id,
            engine=row.engine,
            geo=row.geo,
            persona=row.persona,
            date=row.date,
            mention_rate=row.mention_rate,
            citation_rate=row.citation_rate,
            avg_position=row.avg_position,
            sentiment_score=row.sentiment_score,
            share_of_voice=row.share_of_voice,
            n_samples=row.n_samples,
            ci_low=row.ci_low,
            ci_high=row.ci_high,
        )
        for row in snapshot_rows
    ]

    source_mix = _brand_source_mix(session, tenant_id=tenant_id, brand_id=brand_id)

    opportunities = build_opportunities(
        brand=brand,
        snapshots=snapshots,
        reports=[],  # W3: source opportunities need the candidate-sourcing crawler (out of scope)
        source_mix=source_mix,
    )

    # Idempotent refresh of the OPEN queue. Delete first (before staging inserts) so the DELETE and
    # the fresh INSERTs commit as one transaction, and only the generated-not-yet-acted rows are
    # replaced -- acted/dismissed rows survive.
    session.query(db.Opportunity).filter(
        db.Opportunity.tenant_id == tenant_id,
        db.Opportunity.brand_id == brand_id,
        db.Opportunity.status == "open",
    ).delete(synchronize_session=False)

    for opp in opportunities:
        session.add(
            db.Opportunity(
                id=opp.id,
                tenant_id=brand.tenant_id,
                brand_id=brand.id,
                title=opp.title,
                rationale=opp.rationale,
                engine=opp.engine,
                est_impact=opp.est_impact,
                source_gap=opp.source_gap,
                status="open",
            )
        )
    session.commit()

    logger.info(
        "generated opportunities tenant_id=%s brand_id=%s count=%d",
        tenant_id,
        brand_id,
        len(opportunities),
    )
    return len(opportunities)


def run_opportunity_refresh_job(*, tenant_id: str, brand_id: str) -> int:
    """Local, in-process refresh of `brand_id`'s opportunities; opens its own `Session`.

    The single unit both the request path (``POST /brands/{id}/opportunities/refresh``, scheduled
    onto a ``BackgroundTasks``) and the ``opportunities`` CLI subcommand call, so the two never
    diverge -- exactly mirroring how ``measurement.trigger.run_measurement_job`` backs both the
    measure endpoint and the schedule CLI. A plain sync function that owns and always closes its own
    session (built from ``settings.database_url``); no AWS/Lambda/EventBridge anywhere. Returns the
    number of opportunities generated. ``get_settings`` is imported by name so tests can patch
    ``gw_geo.orchestration.opportunity_gen.get_settings`` and keep the job hermetic.
    """
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session = Session(engine)
    try:
        count = generate_and_persist_opportunities(
            session=session, tenant_id=tenant_id, brand_id=brand_id
        )
    finally:
        session.close()

    logger.info(
        "opportunity refresh job done tenant_id=%s brand_id=%s count=%d",
        tenant_id,
        brand_id,
        count,
    )
    return count
