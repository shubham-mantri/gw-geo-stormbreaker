"""Dashboards feed query module (m1-design.md §5) -- tenant-scoped read layer.

A read-only aggregation layer over `visibility_snapshot` (and `citation` for the source mix),
consumed later by the M2 API + `web/` dashboard (docs/ui-spec.md). Reads `visibility_snapshot`
directly for now; M1-T15 adds a `visibility_rollup` fast path with the same query shapes.

**Every** query filters `tenant_id` (and `brand_id`) explicitly -- there is no shared
tenant-scoping wrapper here (unlike `TenantScopedSession`, which only covers `Brand`), so each
function builds its own `select(...)` with an explicit `tenant_id` predicate, matching the
explicit-filter pattern used in `measurement/runner.py` and `common/budget.py`. `since`/`until`
are ISO `YYYY-MM-DD` strings, inclusive on both ends.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from gw_geo.common.db import Citation, VisibilitySnapshot


def _snapshot_rows_by_date(
    session: Session,
    *,
    tenant_id: str,
    brand_id: str,
    since: str,
    until: str,
    engine: str | None = None,
    geo: str | None = None,
    persona: str | None = None,
) -> dict[str, list[VisibilitySnapshot]]:
    """Tenant-scoped `VisibilitySnapshot` rows in `[since, until]`, grouped by `date`.

    `date` is a plain `YYYY-MM-DD` string column, so the inclusive window is a simple
    lexicographic `since <= date <= until` range (ISO dates sort the same lexicographically and
    chronologically). Groups preserve the ascending `date` order of the underlying query.
    """
    stmt = select(VisibilitySnapshot).where(
        VisibilitySnapshot.tenant_id == tenant_id,
        VisibilitySnapshot.brand_id == brand_id,
        VisibilitySnapshot.date >= since,
        VisibilitySnapshot.date <= until,
    )
    if engine is not None:
        stmt = stmt.where(VisibilitySnapshot.engine == engine)
    if geo is not None:
        stmt = stmt.where(VisibilitySnapshot.geo == geo)
    if persona is not None:
        stmt = stmt.where(VisibilitySnapshot.persona == persona)
    stmt = stmt.order_by(VisibilitySnapshot.date)

    grouped: dict[str, list[VisibilitySnapshot]] = defaultdict(list)
    for row in session.execute(stmt).scalars():
        grouped[row.date].append(row)
    return grouped


def _sample_weighted(
    rows: list[VisibilitySnapshot], get: Callable[[VisibilitySnapshot], float]
) -> float:
    """`n_samples`-weighted average of `get(row)` over `rows`; `0.0` if their samples sum to 0."""
    total_n = sum(row.n_samples for row in rows)
    if not total_n:
        return 0.0
    return sum(get(row) * row.n_samples for row in rows) / total_n


def _merge_snapshot_rows(date: str, rows: list[VisibilitySnapshot]) -> dict[str, Any]:
    """Collapse same-date `VisibilitySnapshot` rows (e.g. several engines) into one dict.

    Rates/sentiment are sample-weighted across `rows`; `avg_position` is sample-weighted only
    over the rows that have one (a `None` avg_position row contributes no position evidence);
    `n_samples` is the summed sample size across all rows for the date.
    """
    position_numerator = 0.0
    position_n = 0
    for row in rows:
        row_position = row.avg_position
        if row_position is not None:
            position_numerator += row_position * row.n_samples
            position_n += row.n_samples
    avg_position = position_numerator / position_n if position_n else None

    return {
        "date": date,
        "mention_rate": _sample_weighted(rows, lambda row: row.mention_rate),
        "citation_rate": _sample_weighted(rows, lambda row: row.citation_rate),
        "avg_position": avg_position,
        "sentiment_score": _sample_weighted(rows, lambda row: row.sentiment_score),
        "n_samples": sum(row.n_samples for row in rows),
    }


def visibility_timeseries(
    session: Session,
    *,
    tenant_id: str,
    brand_id: str,
    engine: str | None = None,
    geo: str | None = None,
    persona: str | None = None,
    since: str,
    until: str,
) -> list[dict[str, Any]]:
    """Daily mention/citation/position/sentiment series for one brand, one row per `date`.

    Optionally narrowed to one `engine`/`geo`/`persona`. When left unset and more than one
    snapshot row shares a `date` (e.g. several engines), those rows are collapsed into a single
    sample-weighted row so the result is always exactly one row per date.
    """
    grouped = _snapshot_rows_by_date(
        session,
        tenant_id=tenant_id,
        brand_id=brand_id,
        since=since,
        until=until,
        engine=engine,
        geo=geo,
        persona=persona,
    )
    return [_merge_snapshot_rows(date, rows) for date, rows in grouped.items()]


def share_of_voice_trend(
    session: Session, *, tenant_id: str, brand_id: str, since: str, until: str
) -> list[dict[str, Any]]:
    """Daily `share_of_voice` trend for one brand, sample-weighted across engines/geo/persona."""
    grouped = _snapshot_rows_by_date(
        session, tenant_id=tenant_id, brand_id=brand_id, since=since, until=until
    )
    return [
        {
            "date": date,
            "share_of_voice": _sample_weighted(rows, lambda row: row.share_of_voice),
            "n_samples": sum(row.n_samples for row in rows),
        }
        for date, rows in grouped.items()
    ]


def _inclusive_date_bounds(since: str, until: str) -> tuple[datetime, datetime]:
    """`[since, until]` inclusive UTC day bounds as a half-open `(start, end)` datetime range."""
    start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    return start, end


def citation_source_mix(
    session: Session, *, tenant_id: str, brand_id: str, since: str, until: str
) -> dict[str, Any]:
    """`{source_type: fraction}` mix of citation volume in `[since, until]`, `seen_count`-weighted.

    `citation` has no per-day `date` column (TRD §4) -- only the running `first_seen`/`last_seen`
    timestamps -- so a row counts as "in the window" when its observed activity span overlaps
    the inclusive `[since, until]` UTC day range. Each matching row contributes its `seen_count`
    (not just 1) to its `source_type`'s total, so a heavily-repeated citation weighs more than a
    one-off, mirroring how often AI answers actually surfaced that source. Returns `{}` when no
    citations fall in the window (nothing to divide by).
    """
    start, end = _inclusive_date_bounds(since, until)
    stmt = select(Citation).where(
        Citation.tenant_id == tenant_id,
        Citation.brand_id == brand_id,
        Citation.first_seen < end,
        Citation.last_seen >= start,
    )

    counts: dict[str, int] = defaultdict(int)
    for row in session.execute(stmt).scalars():
        counts[row.source_type] += row.seen_count

    total = sum(counts.values())
    if not total:
        return {}
    return {source_type: count / total for source_type, count in counts.items()}
