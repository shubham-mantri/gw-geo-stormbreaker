"""Brand management + overview endpoints (ui-spec.md §6, §3.1; m2-design.md §3) -- brand
onboarding/listing and the Overview screen's landing data.

Supersedes T04's temporary ``/brands`` stub in ``app.py`` (kept there only to exercise the
tenancy/RBAC deps before this router existed; ``app.py`` now mounts this router instead).

``GET``/``POST /brands`` are tenant-scoped via ``scoped_session`` (``TenantScopedSession`` derives
its tenant from the token, never a client-supplied value -- TRD §7); ``POST`` additionally requires
``role >= editor``. ``overview`` composes the M1 feed layer (``measurement.feed`` --
``share_of_voice_trend`` for ``sov``/``trend``, ``visibility_timeseries`` for ``mention_rate``) with
the attribution layer (``attribution.pipeline.pipeline_view`` for ``pipeline``/``leads``). Like
``routers/visibility.py``, a brand not owned by the caller's tenant raises :class:`LookupError`
(mapped to **404** by ``app.py``'s handler -- never a 403, so a foreign brand's existence is never
confirmed to the caller).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as SASession

from gw_geo.api.auth import Principal
from gw_geo.api.deps import get_current_principal, get_db_session, require_role, scoped_session
from gw_geo.api.schemas import (
    BrandCreate,
    BrandCreated,
    BrandOut,
    OverviewOut,
    OverviewTrendPoint,
)
from gw_geo.attribution.pipeline import pipeline_view
from gw_geo.common.db import Brand, TenantScopedSession
from gw_geo.measurement import feed

router = APIRouter(tags=["brands"])

_RANGE_RE = re.compile(r"^(\d+)d$")
_DEFAULT_RANGE_DAYS = 30


def _since_until(range_param: str | None) -> tuple[str, str]:
    """Resolve a ``range`` query value (e.g. ``"30d"``) to inclusive ``(since, until)`` ISO dates.

    Mirrors ``routers/visibility.py``'s identical helper -- private to that module (and this one),
    so reimplemented here rather than imported, matching the established convention for these small
    API-layer date helpers (see that module's docstring for the same call on
    ``measurement.feed._inclusive_date_bounds``). An unrecognized/missing value falls back to
    :data:`_DEFAULT_RANGE_DAYS` rather than erroring.
    """
    match = _RANGE_RE.match(range_param) if range_param else None
    days = max(int(match.group(1)), 1) if match else _DEFAULT_RANGE_DAYS
    until_date = datetime.now(timezone.utc).date()
    since_date = until_date - timedelta(days=days - 1)
    return since_date.isoformat(), until_date.isoformat()


def _ensure_brand_owned(scoped: TenantScopedSession, brand_id: str) -> None:
    """Raise :class:`LookupError` unless `brand_id` belongs to the caller's tenant.

    Mirrors ``routers/visibility.py``'s identical helper: "doesn't exist" and "exists but belongs
    to another tenant" deliberately collapse to the same 404 response, so a foreign brand's
    existence is never confirmed to the caller.
    """
    owned = scoped.query_brands().filter(Brand.id == brand_id).first() is not None
    if not owned:
        raise LookupError(f"brand {brand_id!r} not found")


def _weighted_avg(rows: list[dict[str, Any]], value_key: str) -> float:
    """`n_samples`-weighted average of `row[value_key]` over `rows`; `0.0` if samples sum to 0.

    Collapses a feed helper's per-day series (``share_of_voice_trend``, ``visibility_timeseries``)
    into the single window-level number an overview KPI tile shows -- the same sample-weighting
    convention ``measurement.feed``/``routers/visibility.py`` use for every other summary metric.
    """
    total_n = sum(row["n_samples"] for row in rows)
    if not total_n:
        return 0.0
    return float(sum(row[value_key] * row["n_samples"] for row in rows) / total_n)


@router.get("/brands", response_model=list[BrandOut])
def list_brands(scoped: Annotated[TenantScopedSession, Depends(scoped_session)]) -> list[BrandOut]:
    """``GET /brands`` (ui-spec §6) -- the authed tenant's brands only."""
    return [
        BrandOut(id=b.id, name=b.name, domain=b.domain, competitors=list(b.competitors))
        for b in scoped.query_brands()
    ]


@router.post(
    "/brands",
    status_code=201,
    response_model=BrandCreated,
    dependencies=[Depends(require_role("editor"))],
)
def create_brand(
    body: BrandCreate, scoped: Annotated[TenantScopedSession, Depends(scoped_session)]
) -> BrandCreated:
    """``POST /brands`` (ui-spec §6) -- onboard a brand for the authed tenant; requires >= editor
    (a ``viewer`` token -> 403).

    ``seed_topics`` is accepted for the onboarding flow's prompt-discovery kick-off
    (``measurement.discover.build_prompt_set``, M0-T11) but is not invoked here -- wiring that
    (necessarily async/live-engine) kick-off is future work, flagged in the task report. A brand can
    always be onboarded with no prompts yet and have them added later via
    ``GET``/``POST /brands/{id}/prompts`` (T16).
    """
    brand = Brand(
        id=uuid4().hex,
        tenant_id=scoped.tenant_id,
        name=body.name,
        domain=body.domain,
        competitors=list(body.competitors),
    )
    scoped.add(brand)
    scoped.commit()
    return BrandCreated(id=brand.id)


@router.get("/brands/{brand_id}/overview", response_model=OverviewOut)
def get_overview(
    brand_id: str,
    scoped: Annotated[TenantScopedSession, Depends(scoped_session)],
    session: Annotated[SASession, Depends(get_db_session)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    range: str = "30d",
) -> OverviewOut:
    """``GET /brands/{brand_id}/overview`` (ui-spec §3.1) -- landing-screen KPIs + SoV trend.

    A brand not owned by the caller's tenant raises :class:`LookupError` (-> 404).

    ``trend``'s ``competitor`` point is the honest complement ``1 - you``:
    ``visibility_snapshot.share_of_voice`` (``measurement.aggregate.aggregate``) is already
    ``your mentions / (your + every competitor's mentions)`` -- one aggregate figure with no entity
    linkage to any *one* named competitor (the same data-model limitation ``routers/visibility.py``
    documents for its ``sources`` endpoint's ``competitor_pcts``). Reporting the combined "everyone
    else" share is therefore the honest reading of the ui-spec's "you vs top competitor" wording
    given what the data actually supports, rather than fabricating a single-named-competitor
    breakdown (PRD NG1 non-overclaim rule) -- flagged in the task report for a future task once
    citations/extractions carry per-competitor entity linkage.
    """
    _ensure_brand_owned(scoped, brand_id)
    since, until = _since_until(range)

    sov_rows = feed.share_of_voice_trend(
        session, tenant_id=principal.tenant_id, brand_id=brand_id, since=since, until=until
    )
    mention_rows = feed.visibility_timeseries(
        session, tenant_id=principal.tenant_id, brand_id=brand_id, since=since, until=until
    )
    pipeline = pipeline_view(
        session, tenant_id=principal.tenant_id, brand_id=brand_id, since=since, until=until
    )

    return OverviewOut(
        sov=_weighted_avg(sov_rows, "share_of_voice"),
        mention_rate=_weighted_avg(mention_rows, "mention_rate"),
        pipeline=pipeline["influenced"],
        leads=pipeline["leads"],
        trend=[
            OverviewTrendPoint(
                date=row["date"],
                you=row["share_of_voice"],
                competitor=max(0.0, 1.0 - row["share_of_voice"]),
            )
            for row in sov_rows
        ],
    )
