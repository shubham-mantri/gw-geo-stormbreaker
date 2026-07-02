"""Pipeline + alerts read endpoints (ui-spec.md §6, §3.6/§3.7; m2-design.md §3) -- the payoff
screen (revenue from AI search) and the passive drift/wins monitoring feed.

Both endpoints are tenant-scoped: ``scoped_session`` resolves the caller's brand ownership, and a
brand not owned by the token's tenant raises :class:`LookupError` (mapped to **404** by
``app.py``'s handler -- never a 403, so a foreign brand's existence is never confirmed to the
caller), matching ``routers/visibility.py``/``routers/brands.py``'s convention.

``pipeline`` composes no data of its own -- it returns
:func:`gw_geo.attribution.pipeline.pipeline_view` (T10)'s output **verbatim**; the response model
only validates the shape (m2-design §1 "non-overclaim rule": the method breakdown + confidence note
must always accompany the headline numbers).

``alerts`` has no backing table of its own; it is computed on read from two sources:

* **Drift** -- the system-level ``drift_event`` table (m1-design §6: no ``tenant_id``/``brand_id``
  at all, since engine drift is a property of the engine/canary, not of any one tenant). Every
  tenant that owns *some* brand sees the same drift feed; a breached row maps to ``red``, else
  ``yellow`` (the writer, ``orchestration/drift.py``, currently only ever persists breached rows,
  but the mapping stays meaningful if a future writer logs sub-threshold observations too).
* **Wins** -- "new #1 recommendation" (ui-spec §3.7): a tracked prompt whose most recent probe
  ranks the brand #1 when its immediately-preceding probe did not (a genuine transition, not merely
  "currently #1" -- there is no persisted alert log to dedup against, so re-firing on every read
  would flood the feed forever once a brand reaches #1 and stays there). Flagged in the task report
  as a modelling choice, not a TRD-pinned contract: the ui-spec's other two example alerts (a
  competitor newly appearing) would need citation-to-competitor entity linkage that does not exist
  yet (see ``routers/visibility.py``'s ``sources`` docstring for the same gap).
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from gw_geo.api.auth import Principal
from gw_geo.api.deps import get_current_principal, get_db_session, scoped_session
from gw_geo.api.schemas import AlertOut, PipelineOut
from gw_geo.attribution.pipeline import pipeline_view
from gw_geo.common.db import (
    AnswerExtraction,
    Brand,
    DriftEvent,
    ProbeRun,
    Prompt,
    TenantScopedSession,
)

router = APIRouter(tags=["pipeline"])

_RANGE_RE = re.compile(r"^(\d+)d$")
_DEFAULT_RANGE_DAYS = 30


def _since_until(range_param: str | None) -> tuple[str, str]:
    """Resolve a ``range`` query value (e.g. ``"30d"``) to inclusive ``(since, until)`` ISO dates.

    Mirrors ``routers/visibility.py``/``routers/brands.py``'s identical helper -- private to each
    of those modules (and this one), matching the established convention for these small API-layer
    date helpers. An unrecognized/missing value falls back to :data:`_DEFAULT_RANGE_DAYS` rather
    than erroring.
    """
    match = _RANGE_RE.match(range_param) if range_param else None
    days = max(int(match.group(1)), 1) if match else _DEFAULT_RANGE_DAYS
    until_date = datetime.now(timezone.utc).date()
    since_date = until_date - timedelta(days=days - 1)
    return since_date.isoformat(), until_date.isoformat()


def _ensure_brand_owned(scoped: TenantScopedSession, brand_id: str) -> None:
    """Raise :class:`LookupError` unless `brand_id` belongs to the caller's tenant.

    Mirrors ``routers/visibility.py``/``routers/brands.py``'s identical helper: "doesn't exist" and
    "exists but belongs to another tenant" deliberately collapse to the same 404 response, so a
    foreign brand's existence is never confirmed to the caller.
    """
    owned = scoped.query_brands().filter(Brand.id == brand_id).first() is not None
    if not owned:
        raise LookupError(f"brand {brand_id!r} not found")


def _as_utc(ts: datetime) -> datetime:
    """Normalize to a tz-aware UTC ``datetime``.

    ``DriftEvent.ts`` is a naive ``DateTime`` column while ``ProbeRun.ts`` is
    ``DateTime(timezone=True)``; sorting a naive and an aware datetime together raises
    ``TypeError``. (SQLite, used in tests, drops tzinfo from both either way, so this only bites on
    a real Postgres deploy -- normalized here regardless so the two alert sources are always safely
    comparable and the API's ``ts`` values are consistently offset-bearing.)
    """
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def _drift_message(event: DriftEvent) -> str:
    """Plain-language rendering of a drift event, e.g. "chatgpt visibility -40% vs. baseline
    (canary chatgpt-crm-baseline) -- likely algorithm change; re-optimizing" (ui-spec §3.7 mockup).
    """
    pct = f"{event.drop:.0%}"
    verdict = (
        "likely algorithm change; re-optimizing" if event.breached else "below alert threshold"
    )
    return f"{event.engine} visibility -{pct} vs. baseline (canary {event.canary_id}) -- {verdict}"


def _drift_alerts(session: SASession) -> list[AlertOut]:
    """Every system-level :class:`DriftEvent` row, mapped to severity (``breached`` -> ``red``,
    else ``yellow``). Not tenant/brand filtered -- see module docstring."""
    events = session.execute(select(DriftEvent)).scalars().all()
    return [
        AlertOut(
            severity="red" if event.breached else "yellow",
            message=_drift_message(event),
            ts=_as_utc(event.ts),
        )
        for event in events
    ]


def _win_alerts(session: SASession, *, tenant_id: str, brand_id: str) -> list[AlertOut]:
    """"Now #1 recommendation" wins (ui-spec §3.7) -- see module docstring for the transition rule.

    Joins ``prompt`` -> ``probe_run`` -> ``answer_extraction`` (the same tables
    ``routers/visibility.py``'s ``prompts`` table reads), restricted to ``status="ok"`` runs, and
    keeps each prompt's two most recent extractions (ordered by ``probe_run.ts`` descending) to
    detect the "just became #1" transition.
    """
    stmt = (
        select(Prompt.id, Prompt.text, AnswerExtraction.position, ProbeRun.ts)
        .join(ProbeRun, ProbeRun.prompt_id == Prompt.id)
        .join(AnswerExtraction, AnswerExtraction.probe_run_id == ProbeRun.id)
        .where(
            Prompt.tenant_id == tenant_id,
            Prompt.brand_id == brand_id,
            ProbeRun.status == "ok",
        )
        .order_by(Prompt.id, ProbeRun.ts.desc())
    )
    latest_two: dict[str, list[tuple[int | None, datetime]]] = defaultdict(list)
    texts: dict[str, str] = {}
    for prompt_id, text, position, ts in session.execute(stmt):
        texts[prompt_id] = text
        if len(latest_two[prompt_id]) < 2:
            latest_two[prompt_id].append((position, ts))

    wins: list[AlertOut] = []
    for prompt_id, rows in latest_two.items():
        newest_position, newest_ts = rows[0]
        previous_position = rows[1][0] if len(rows) > 1 else None
        if newest_position == 1 and previous_position != 1:
            wins.append(
                AlertOut(
                    severity="green",
                    message=f'Now #1 recommendation for "{texts[prompt_id]}"',
                    ts=_as_utc(newest_ts),
                )
            )
    return wins


@router.get("/brands/{brand_id}/pipeline", response_model=PipelineOut)
def get_pipeline(
    brand_id: str,
    scoped: Annotated[TenantScopedSession, Depends(scoped_session)],
    session: Annotated[SASession, Depends(get_db_session)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    range: str = "30d",
) -> PipelineOut:
    """``GET /brands/{brand_id}/pipeline`` (ui-spec §3.6) -- the revenue/payoff view.

    ``pipeline_view`` takes a *raw* ``Session`` plus an explicit ``tenant_id`` (it scopes
    internally, TRD §7); ``scoped`` is used only for the brand-ownership check. A brand not owned
    by the caller's tenant raises :class:`LookupError` (-> 404).
    """
    _ensure_brand_owned(scoped, brand_id)
    since, until = _since_until(range)
    return PipelineOut(
        **pipeline_view(
            session, tenant_id=principal.tenant_id, brand_id=brand_id, since=since, until=until
        )
    )


@router.get("/brands/{brand_id}/alerts", response_model=list[AlertOut])
def get_alerts(
    brand_id: str,
    scoped: Annotated[TenantScopedSession, Depends(scoped_session)],
    session: Annotated[SASession, Depends(get_db_session)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> list[AlertOut]:
    """``GET /brands/{brand_id}/alerts`` (ui-spec §3.7) -- drift + win monitoring feed, newest
    first. A brand not owned by the caller's tenant raises :class:`LookupError` (-> 404) -- see
    module docstring for why the drift alerts themselves are not actually brand-specific.
    """
    _ensure_brand_owned(scoped, brand_id)
    alerts = _drift_alerts(session) + _win_alerts(
        session, tenant_id=principal.tenant_id, brand_id=brand_id
    )
    alerts.sort(key=lambda alert: alert.ts, reverse=True)
    return alerts
