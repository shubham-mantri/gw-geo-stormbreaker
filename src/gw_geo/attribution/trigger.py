"""Local attribution-reconcile trigger (W4): run the fuzzy attribution *writers* over a brand's
already-captured sessions/leads and persist the ``attribution_link`` rows the pipeline reads.

The lead-capture pixel (``POST /lead-capture/collect``) only *ingests* raw ``session``/``lead``
rows; it never classifies referrers or writes attribution edges. The three fuzzy mechanisms that
turn those raw rows into ``attribution_link`` rows are a **separate batch** (m2-design §2.2-§2.4):

* :func:`gw_geo.attribution.referral.link_direct` -- AI-referrer -> ``direct`` links (mechanism 1);
* :func:`gw_geo.attribution.linkage.link_citations` -- landing-URL<->citation -> ``citation_linked``
  (mechanism 2);
* :func:`gw_geo.attribution.assisted.assisted_credit` -- self-report + branded-lift -> ``assisted``
  (mechanism 3).

Nothing ran that batch locally before -- so ``GET /brands/{id}/pipeline`` (which *reads* persisted
links, only computing holdout incrementality live) reported zero attributed value no matter how many
leads the pixel captured. :func:`reconcile_attribution` is that missing batch; it is the single unit
both the request path (``POST /brands/{id}/attribution/reconcile``, scheduled onto a
``BackgroundTasks``) and the ``reconcile`` CLI subcommand call -- exactly mirroring how
``measurement.trigger.run_measurement_job`` / ``orchestration.opportunity_gen.run_opportunity_
refresh_job`` back both their endpoint and their CLI, so the two never diverge.

Mechanism order is strongest-first (``direct`` -> ``citation_linked`` -> ``assisted``): ``link_direct``
stamps ``session.engine`` first, which ``link_citations`` then uses to disambiguate which cited
answer to credit. Mechanism 4 (holdout incrementality) writes no link and is intentionally *not*
run here -- ``pipeline.pipeline_view`` measures it live.

FK-safety (real Postgres enforces FKs; SQLite defaults them off -- see the measurement-runner /
opportunity-gen fixes): every parent this batch's ``attribution_link`` children reference
(``session``/``lead``) was written and **committed** by an earlier ``/lead-capture/collect`` request
before this batch runs, so a child never precedes its parent. The batch itself inserts only leaf
``attribution_link`` rows (and updates ``session.engine`` in place, not an insert); a missing or
cross-tenant brand short-circuits to an all-zero result rather than touching the DB at all.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm import Session as SASession

from gw_geo.attribution.assisted import assisted_credit
from gw_geo.attribution.linkage import link_citations
from gw_geo.attribution.referral import link_direct
from gw_geo.common.config import get_settings
from gw_geo.common.db import Brand, TenantScopedSession
from gw_geo.measurement.feed import share_of_voice_trend

logger = logging.getLogger(__name__)

# Default reconcile look-back when the caller names no window: the trailing 90 days (inclusive),
# generous enough to sweep every recently-captured session/lead. Same inclusive-ends,
# ``since = until - (days - 1)`` convention the API routers' ``_since_until`` helpers use.
_LOOKBACK_DAYS = 90


def _default_window() -> tuple[str, str]:
    """``(since, until)`` ISO dates for the default trailing :data:`_LOOKBACK_DAYS`-day window."""
    until = datetime.now(timezone.utc).date()
    since = until - timedelta(days=_LOOKBACK_DAYS - 1)
    return since.isoformat(), until.isoformat()


def reconcile_attribution(
    *, session: SASession, tenant_id: str, brand_id: str, since: str, until: str
) -> dict[str, int]:
    """Run the three fuzzy attribution writers for ``brand_id`` over ``[since, until]``; return the
    per-method link counts ``{"direct": n, "citation_linked": n, "assisted": n}``.

    ``session`` is a *raw* SQLAlchemy ``Session``; it is wrapped in a
    :class:`~gw_geo.common.db.TenantScopedSession` bound to ``tenant_id`` here (TRD §7), so every
    read/write is tenant-safe. The branded-lift arm of ``assisted_credit`` is fed the brand's
    ``share_of_voice`` trend (``measurement.feed.share_of_voice_trend``) -- the same series the
    ``/overview`` composition uses -- so a correlation can be modelled where visibility data exists
    (empty series -> no ``modeled`` links, only self-reported ones). A missing or cross-tenant brand
    is a no-op returning all-zero counts (mirrors ``opportunity_gen``)."""
    brand_row = session.get(Brand, brand_id)
    if brand_row is None or brand_row.tenant_id != tenant_id:
        logger.warning(
            "brand_id=%r not found for tenant_id=%r; no attribution reconciled", brand_id, tenant_id
        )
        return {"direct": 0, "citation_linked": 0, "assisted": 0}

    scoped = TenantScopedSession(session, tenant_id)
    visibility_series = share_of_voice_trend(
        session, tenant_id=tenant_id, brand_id=brand_id, since=since, until=until
    )

    # Strongest-first: link_direct stamps session.engine, which link_citations then reads to
    # disambiguate the credited citation. Each writer commits its own links internally.
    direct = link_direct(scoped, tenant_id=tenant_id, brand_id=brand_id, since=since, until=until)
    citations = link_citations(
        scoped, tenant_id=tenant_id, brand_id=brand_id, since=since, until=until
    )
    assisted = assisted_credit(
        scoped,
        tenant_id=tenant_id,
        brand_id=brand_id,
        since=since,
        until=until,
        visibility_series=visibility_series,
    )

    counts = {
        "direct": len(direct),
        "citation_linked": len(citations),
        "assisted": len(assisted),
    }
    logger.info(
        "attribution reconcile tenant_id=%s brand_id=%s window=%s..%s counts=%s",
        tenant_id,
        brand_id,
        since,
        until,
        counts,
    )
    return counts


def run_attribution_reconcile_job(
    *, tenant_id: str, brand_id: str, since: str | None = None, until: str | None = None
) -> dict[str, int]:
    """Local, in-process attribution reconcile for ``brand_id``; opens (and always closes) its own
    ``Session`` from ``settings.database_url``.

    The single unit both the request path (``POST /brands/{id}/attribution/reconcile``, scheduled
    onto a ``BackgroundTasks``) and the ``reconcile`` CLI subcommand call, so the two never diverge
    -- exactly mirroring ``measurement.trigger.run_measurement_job`` /
    ``orchestration.opportunity_gen.run_opportunity_refresh_job``. A plain sync function safe to hand
    to a ``BackgroundTasks``; no AWS/Lambda anywhere. ``since``/``until`` default to the trailing
    :data:`_LOOKBACK_DAYS`-day window when omitted. Returns the per-method link counts.
    ``get_settings`` is imported by name so tests can patch
    ``gw_geo.attribution.trigger.get_settings`` and keep the job hermetic."""
    settings = get_settings()
    if since is None or until is None:
        default_since, default_until = _default_window()
        since = since or default_since
        until = until or default_until

    engine = create_engine(settings.database_url)
    session = Session(engine)
    try:
        counts = reconcile_attribution(
            session=session, tenant_id=tenant_id, brand_id=brand_id, since=since, until=until
        )
    finally:
        session.close()

    logger.info(
        "attribution reconcile job done tenant_id=%s brand_id=%s counts=%s",
        tenant_id,
        brand_id,
        counts,
    )
    return counts
