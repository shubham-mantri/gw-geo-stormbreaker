"""Visibility + sources read endpoints (ui-spec.md §6, §3.2/§3.3; m2-design.md §3) -- the
per-engine deep-dive and citation-source map the ``web/`` Visibility/Sources screens render.

Both endpoints are tenant-scoped: ``scoped_session`` resolves the caller's brand ownership, and a
brand not owned by the token's tenant raises :class:`LookupError` (mapped to **404** by
``app.py``'s handler -- never a 403, so a foreign brand's existence is never confirmed to the
caller). Aggregation queries then run on a plain (unscoped) ``Session`` with an explicit
``tenant_id``/``brand_id`` filter, matching ``measurement/feed.py``'s own convention (see that
module's docstring) rather than ``TenantScopedSession.query`` -- feed-style read layers filter
explicitly because they aggregate, not just fetch entities.

Data-model notes (see the task report's CONCERNS for the full writeup):

* ``visibility``'s per-engine ``ci``/``n_samples`` are read straight off ``visibility_snapshot``
  (TRD §3's Wilson-interval columns). ``visibility_rollup`` (M1-T15's fast path, which
  ``feed.visibility_timeseries`` prefers when populated) carries no CI columns at all, so the
  per-engine summary row always reads snapshots directly; only ``trend`` uses the feed function
  (and so may use the rollup fast path).
* ``prompts`` has no feed helper to source from -- ``visibility_snapshot`` has no ``prompt_id``
  (``aggregate()`` collapses every prompt into one (engine, geo, persona, date) row) -- so this
  joins ``prompt`` -> ``probe_run`` -> ``answer_extraction`` directly, the same tables
  ``measurement/runner.py`` writes.
* ``sources`` needs one row *per domain*, but ``feed.citation_source_mix`` aggregates ``citation``
  by ``source_type`` only (losing domain granularity); this queries ``citation`` directly, grouped
  by ``domain``, using the same tenant/window filtering ``citation_source_mix`` applies.
  ``competitor_pcts`` is always ``{}``: ``citation`` has no link to *which* entity (the tracked
  brand or a named competitor) a citation's answer was actually about, so there is no data source
  for a real per-competitor share yet -- returning fabricated numbers would violate the
  white-hat/no-overclaim rule (PRD NG1), so this is left honestly empty pending a future task.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from gw_geo.api.deps import get_db_session, scoped_session
from gw_geo.api.schemas import (
    SourceOut,
    VisibilityEngineOut,
    VisibilityOut,
    VisibilityPromptOut,
    VisibilityTrendPoint,
)
from gw_geo.common.db import (
    AnswerExtraction,
    Brand,
    Citation,
    ProbeRun,
    Prompt,
    TenantScopedSession,
    VisibilitySnapshot,
)
from gw_geo.measurement import feed

router = APIRouter(tags=["visibility"])

_RANGE_RE = re.compile(r"^(\d+)d$")
_DEFAULT_RANGE_DAYS = 30


def _since_until(range_param: str | None) -> tuple[str, str]:
    """Resolve a ``range`` query value (e.g. ``"30d"``) to inclusive ``(since, until)`` ISO dates.

    ``until`` is always "today" (UTC); ``since`` is ``days - 1`` earlier so the window spans
    exactly `days` calendar days inclusive. An unrecognized/missing value falls back to
    :data:`_DEFAULT_RANGE_DAYS` rather than erroring -- a read endpoint backing a dashboard widget
    should degrade gracefully on a malformed filter, not 4xx the whole screen.
    """
    match = _RANGE_RE.match(range_param) if range_param else None
    days = max(int(match.group(1)), 1) if match else _DEFAULT_RANGE_DAYS
    until_date = datetime.now(timezone.utc).date()
    since_date = until_date - timedelta(days=days - 1)
    return since_date.isoformat(), until_date.isoformat()


def _inclusive_date_bounds(since: str, until: str) -> tuple[datetime, datetime]:
    """`[since, until]` inclusive UTC day bounds as a half-open `(start, end)` datetime range.

    Mirrors ``measurement.feed._inclusive_date_bounds`` (private to that module, so reimplemented
    here rather than imported).
    """
    start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    return start, end


def _ensure_brand_owned(scoped: TenantScopedSession, brand_id: str) -> None:
    """Raise :class:`LookupError` unless `brand_id` belongs to the caller's tenant.

    Never distinguishes "doesn't exist" from "exists but belongs to another tenant" in the
    response -- both collapse to the same 404 (via ``app.py``'s exception handler), so a foreign
    brand's existence is never confirmed to the caller.
    """
    owned = scoped.query_brands().filter(Brand.id == brand_id).first() is not None
    if not owned:
        raise LookupError(f"brand {brand_id!r} not found")


def _weighted(rows: list[VisibilitySnapshot], get: Callable[[VisibilitySnapshot], float]) -> float:
    """`n_samples`-weighted average of `get(row)` over `rows`; `0.0` if their samples sum to 0.

    Mirrors ``measurement.feed._sample_weighted`` (private to that module) -- applied here to
    ``mention_rate``/``citation_rate``/``sentiment_score`` *and* ``ci_low``/``ci_high``, since a
    per-engine summary row can span every `visibility_snapshot` row in the window (e.g. one per
    date), not just one.
    """
    total_n = sum(row.n_samples for row in rows)
    if not total_n:
        return 0.0
    return sum(get(row) * row.n_samples for row in rows) / total_n


def _engine_rows(
    session: SASession,
    *,
    tenant_id: str,
    brand_id: str,
    since: str,
    until: str,
    geo: str | None,
    persona: str | None,
) -> list[VisibilityEngineOut]:
    """Per-engine summary rows for ``/visibility``, each carrying ``ci`` + ``n_samples`` sourced
    from ``visibility_snapshot`` (see module docstring for why the rollup fast path can't serve
    this), plus a ``trend`` series from ``feed.visibility_timeseries``.
    """
    stmt = select(VisibilitySnapshot).where(
        VisibilitySnapshot.tenant_id == tenant_id,
        VisibilitySnapshot.brand_id == brand_id,
        VisibilitySnapshot.date >= since,
        VisibilitySnapshot.date <= until,
    )
    if geo is not None:
        stmt = stmt.where(VisibilitySnapshot.geo == geo)
    if persona is not None:
        stmt = stmt.where(VisibilitySnapshot.persona == persona)

    by_engine: dict[str, list[VisibilitySnapshot]] = defaultdict(list)
    for row in session.execute(stmt).scalars():
        by_engine[row.engine].append(row)

    engines: list[VisibilityEngineOut] = []
    for engine, rows in by_engine.items():
        position_sum = 0.0
        position_n = 0
        for row in rows:
            position = row.avg_position
            if position is not None:
                position_sum += position * row.n_samples
                position_n += row.n_samples
        avg_position = position_sum / position_n if position_n else None

        trend_rows = feed.visibility_timeseries(
            session,
            tenant_id=tenant_id,
            brand_id=brand_id,
            engine=engine,
            geo=geo,
            persona=persona,
            since=since,
            until=until,
        )

        engines.append(
            VisibilityEngineOut(
                engine=engine,
                mention_rate=_weighted(rows, lambda r: r.mention_rate),
                ci=(_weighted(rows, lambda r: r.ci_low), _weighted(rows, lambda r: r.ci_high)),
                cited=_weighted(rows, lambda r: r.citation_rate),
                avg_position=avg_position,
                sentiment=_weighted(rows, lambda r: r.sentiment_score),
                n_samples=sum(row.n_samples for row in rows),
                trend=[
                    VisibilityTrendPoint(date=r["date"], mention_rate=r["mention_rate"])
                    for r in trend_rows
                ],
            )
        )
    engines.sort(key=lambda e: e.engine)
    return engines


def _prompt_rows(
    session: SASession,
    *,
    tenant_id: str,
    brand_id: str,
    since: str,
    until: str,
    geo: str | None,
    persona: str | None,
) -> list[VisibilityPromptOut]:
    """Per-prompt mention rate/position for ``/visibility``'s ``prompts`` table (ui-spec §3.2).

    ``visibility_snapshot`` has no ``prompt_id`` -- ``aggregate()`` collapses every prompt into one
    (engine, geo, persona, date) row -- so this joins ``prompt`` -> ``probe_run`` ->
    ``answer_extraction`` directly, restricted to ``status="ok"`` runs (an ``"error"`` run has no
    extraction to join anyway; the filter just documents intent). ``since``/``until`` bound
    ``probe_run.ts``, not a `date` column.
    """
    start, end = _inclusive_date_bounds(since, until)
    stmt = (
        select(Prompt, AnswerExtraction)
        .join(ProbeRun, ProbeRun.prompt_id == Prompt.id)
        .join(AnswerExtraction, AnswerExtraction.probe_run_id == ProbeRun.id)
        .where(
            Prompt.tenant_id == tenant_id,
            Prompt.brand_id == brand_id,
            ProbeRun.status == "ok",
            ProbeRun.ts >= start,
            ProbeRun.ts < end,
        )
    )
    if geo is not None:
        stmt = stmt.where(ProbeRun.geo == geo)
    if persona is not None:
        stmt = stmt.where(ProbeRun.persona == persona)

    texts: dict[str, str] = {}
    mentions: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    position_sum: dict[str, float] = defaultdict(float)
    position_n: dict[str, int] = defaultdict(int)

    for prompt, extraction in session.execute(stmt):
        texts[prompt.id] = prompt.text
        counts[prompt.id] += 1
        if extraction.brand_mentioned:
            mentions[prompt.id] += 1
        position = extraction.position
        if position is not None:
            position_sum[prompt.id] += position
            position_n[prompt.id] += 1

    rows = [
        VisibilityPromptOut(
            prompt_id=prompt_id,
            text=texts[prompt_id],
            mention_rate=mentions[prompt_id] / n,
            avg_position=(
                position_sum[prompt_id] / position_n[prompt_id] if position_n[prompt_id] else None
            ),
            n_samples=n,
        )
        for prompt_id, n in counts.items()
    ]
    rows.sort(key=lambda r: r.prompt_id)
    return rows


def _citation_rows_by_domain(
    session: SASession, *, tenant_id: str, brand_id: str, since: str, until: str
) -> list[SourceOut]:
    """Per-domain citation shares for ``/sources`` (ui-spec §3.3) -- see module docstring for why
    this queries ``citation`` directly (grouped by ``domain``) instead of using
    ``feed.citation_source_mix`` (which aggregates by ``source_type`` only, and would collapse
    every domain of the same type into one fraction). Uses the same tenant/window filtering
    (`first_seen`/`last_seen` overlap with the inclusive `[since, until]` window, `seen_count`
    weighted) that ``citation_source_mix`` applies.
    """
    start, end = _inclusive_date_bounds(since, until)
    stmt = select(Citation).where(
        Citation.tenant_id == tenant_id,
        Citation.brand_id == brand_id,
        Citation.first_seen < end,
        Citation.last_seen >= start,
    )

    seen_by_domain: dict[str, int] = defaultdict(int)
    source_type_votes: dict[str, Counter[str]] = defaultdict(Counter)
    for row in session.execute(stmt).scalars():
        seen_by_domain[row.domain] += row.seen_count
        source_type_votes[row.domain][row.source_type] += row.seen_count

    total = sum(seen_by_domain.values())
    if not total:
        return []

    rows = [
        SourceOut(
            domain=domain,
            source_type=source_type_votes[domain].most_common(1)[0][0],
            you_pct=count / total,
            # No entity linkage on `citation` yet -- see module docstring; never fabricate a
            # competitor share.
            competitor_pcts={},
        )
        for domain, count in seen_by_domain.items()
    ]
    rows.sort(key=lambda r: r.you_pct, reverse=True)
    return rows


@router.get("/brands/{brand_id}/visibility", response_model=VisibilityOut)
def get_visibility(
    brand_id: str,
    scoped: Annotated[TenantScopedSession, Depends(scoped_session)],
    session: Annotated[SASession, Depends(get_db_session)],
    range: str = "30d",
    geo: str | None = None,
    persona: str | None = None,
) -> VisibilityOut:
    """``GET /brands/{brand_id}/visibility`` (ui-spec §3.2) -- per-engine deep dive + prompt table.

    A brand not owned by the caller's tenant raises :class:`LookupError` (-> 404).
    """
    _ensure_brand_owned(scoped, brand_id)
    since, until = _since_until(range)
    return VisibilityOut(
        engines=_engine_rows(
            session,
            tenant_id=scoped.tenant_id,
            brand_id=brand_id,
            since=since,
            until=until,
            geo=geo,
            persona=persona,
        ),
        prompts=_prompt_rows(
            session,
            tenant_id=scoped.tenant_id,
            brand_id=brand_id,
            since=since,
            until=until,
            geo=geo,
            persona=persona,
        ),
    )


@router.get("/brands/{brand_id}/sources", response_model=list[SourceOut])
def get_sources(
    brand_id: str,
    scoped: Annotated[TenantScopedSession, Depends(scoped_session)],
    session: Annotated[SASession, Depends(get_db_session)],
    range: str = "30d",
) -> list[SourceOut]:
    """``GET /brands/{brand_id}/sources`` (ui-spec §3.3) -- citation-source map.

    A brand not owned by the caller's tenant raises :class:`LookupError` (-> 404).
    """
    _ensure_brand_owned(scoped, brand_id)
    since, until = _since_until(range)
    return _citation_rows_by_domain(
        session, tenant_id=scoped.tenant_id, brand_id=brand_id, since=since, until=until
    )
