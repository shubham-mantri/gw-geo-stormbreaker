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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session as SASession

from gw_geo.api.auth import Principal
from gw_geo.api.deps import (
    get_current_principal,
    get_db_session,
    get_settings_dep,
    require_role,
    scoped_session,
)
from gw_geo.api.schemas import (
    AttributionReconcileAccepted,
    AttributionReconcileRequest,
    BrandCreate,
    BrandCreated,
    BrandOut,
    BrandSuggestion,
    BrandSuggestRequest,
    MeasureAccepted,
    MeasureTriggerRequest,
    OpportunityRefreshAccepted,
    OverviewOut,
    OverviewTrendPoint,
    RankingRefreshAccepted,
    RankingRefreshRequest,
)
from gw_geo.attribution.pipeline import pipeline_view
from gw_geo.attribution.trigger import run_attribution_reconcile_job
from gw_geo.common.config import Settings
from gw_geo.common.db import Brand, TenantScopedSession
from gw_geo.common.wiring import configured_engine_names
from gw_geo.content.gateway import build_llm_client, resolve_chat_model
from gw_geo.content.generate import LLMClient
from gw_geo.measurement import feed
from gw_geo.measurement.trigger import run_measurement_job
from gw_geo.onboarding.suggest import suggest_brand_details
from gw_geo.orchestration.opportunity_gen import run_opportunity_refresh_job
from gw_geo.orchestration.ranking_gen import run_ranking_refresh_job
from gw_geo.ranking.fetch import HttpxPageFetcher, PageFetcher

router = APIRouter(tags=["brands"])

_RANGE_RE = re.compile(r"^(\d+)d$")
_DEFAULT_RANGE_DAYS = 30


@dataclass(frozen=True)
class BrandSuggestDeps:
    """The injected collaborators for ``POST /brands/suggest``: the page fetcher + two LLM clients.

    ``llm`` drives the profile + draft research stages (web-search-enabled on the local-Claude
    gateway); ``critic`` drives the (web-search-free) critique/refine stage. Bundled so a test
    overrides one dependency (:func:`get_brand_suggest_deps`) with hermetic fakes, the same seam
    ``routers/content.py`` uses for its ``ContentService``.
    """

    fetcher: PageFetcher
    llm: LLMClient
    critic: LLMClient


def get_brand_suggest_deps(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    session: Annotated[SASession, Depends(get_db_session)],
) -> BrandSuggestDeps:
    """Injected fetcher + research/critic LLMs for ``POST /brands/suggest`` -- the real, config-wired
    collaborators.

    The default is the *live* trio (SSRF-guarded :class:`~gw_geo.ranking.fetch.HttpxPageFetcher` +
    two gateway-selected :class:`~gw_geo.content.generate.LLMClient`\\ s), unlike the raising
    ``content.get_content_service`` default, because constructing these opens **no** connection
    (``HttpxPageFetcher`` only stores config; ``build_llm_client`` returns a client that connects
    lazily on first ``complete``), so it is safe to build at request time. The chat model is the
    DB-stored, operator-selectable one for the active gateway (``resolve_chat_model``; falls back to
    today's constants when unset).

    On the ``local_claude`` gateway the research client is built with ``allow_web_search=True`` so
    the profile/draft stages ground on a real ($0) web search; the critic client is plain. On
    ``portkey``/``direct`` there is no local CLI web search, so the flag is a no-op and both clients
    are plain -- suggest still runs the hardened prompt + critique pass, just without web grounding
    (graceful degrade). Tests override this with hermetic fakes via
    ``app.dependency_overrides[get_brand_suggest_deps]`` -- no live HTTP/LLM call.
    """
    model = resolve_chat_model(session, gateway=settings.llm_gateway, settings=settings)
    return BrandSuggestDeps(
        fetcher=HttpxPageFetcher(),
        llm=build_llm_client(settings, model=model, allow_web_search=True),
        critic=build_llm_client(settings, model=model),
    )


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


@router.post(
    "/brands/suggest",
    response_model=BrandSuggestion,
    dependencies=[Depends(require_role("editor"))],
)
def suggest_brand(
    body: BrandSuggestRequest,
    deps: Annotated[BrandSuggestDeps, Depends(get_brand_suggest_deps)],
) -> BrandSuggestion:
    """``POST /brands/suggest`` (M5 domain-first onboarding) -- auto-fill a brand from its domain.

    From the posted ``domain``, reads the brand **name** off the live site (JSON-LD/``og:site_name``/
    ``<title>``, else a domain heuristic) and asks the LLM for likely **competitors** -- both returned
    as editable suggestions. Requires ``role >= editor`` (a ``viewer`` -> **403**, an unauthenticated
    caller -> **401**), matching ``POST /brands``'s principal requirement (the user who will onboard
    the brand); ``tenant_id`` is derived from the token. Performs **no DB write** -- pure read/suggest.

    Never surfaces a 5xx from a dead site or an unconfigured/failing LLM: :func:`suggest_brand_details`
    is total, degrading to the domain heuristic + an empty competitor list, so onboarding always
    proceeds to manual entry. The only network touched is the user-supplied domain, via the injected
    SSRF-guarded fetcher (see :func:`get_brand_suggest_deps`).

    Registered before the ``/brands/{brand_id}/...`` routes so the static ``/brands/suggest`` path is
    matched literally, never captured as a ``brand_id`` path param.
    """
    return suggest_brand_details(
        domain=body.domain, fetcher=deps.fetcher, llm=deps.llm, critic=deps.critic
    )


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


@router.post("/brands/{brand_id}/measure", status_code=202, response_model=MeasureAccepted)
def trigger_measurement(
    brand_id: str,
    background_tasks: BackgroundTasks,
    principal: Annotated[Principal, Depends(require_role("editor"))],
    scoped: Annotated[TenantScopedSession, Depends(scoped_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    body: MeasureTriggerRequest | None = None,
) -> MeasureAccepted:
    """``POST /brands/{brand_id}/measure`` (W2 live wiring) -- kick off a measurement run.

    Requires ``role >= editor`` (a ``viewer`` -> **403**) and brand ownership under the caller's
    tenant (an unowned/unknown brand -> **404**, never a tenant leak -- same collapse as
    ``get_overview``). The run is **scheduled onto a background task**, never executed inline in the
    request: ``run_measurement_job`` builds the runtime + a fresh session and drives the async
    pipeline out of band, so the request returns **202** immediately.

    ``engines`` / ``n_samples`` / ``geos`` come from the (optional) body, else fall back to every
    API-keyed engine the runtime has configured and the settings defaults. ``tenant_id`` is taken
    from the token (``principal``), never the client. Returns what was scheduled (engines, n).
    """
    _ensure_brand_owned(scoped, brand_id)
    req = body if body is not None else MeasureTriggerRequest()

    engines = req.engines if req.engines else configured_engine_names(settings)
    n_samples = req.n_samples if req.n_samples is not None else settings.default_n_samples
    geos = req.geos if req.geos else list(settings.default_geos)

    background_tasks.add_task(
        run_measurement_job,
        tenant_id=principal.tenant_id,
        brand_id=brand_id,
        engines=engines,
        geos=geos,
        n_samples=n_samples,
        date=req.date,
    )
    return MeasureAccepted(
        status="accepted", brand_id=brand_id, engines=engines, n_samples=n_samples
    )


@router.post(
    "/brands/{brand_id}/opportunities/refresh",
    status_code=202,
    response_model=OpportunityRefreshAccepted,
)
def refresh_opportunities(
    brand_id: str,
    background_tasks: BackgroundTasks,
    principal: Annotated[Principal, Depends(require_role("editor"))],
    scoped: Annotated[TenantScopedSession, Depends(scoped_session)],
) -> OpportunityRefreshAccepted:
    """``POST /brands/{brand_id}/opportunities/refresh`` (W3) -- (re)generate the brand's ranked
    opportunity queue from its live visibility data.

    Requires ``role >= editor`` (a ``viewer`` -> **403**) and brand ownership under the caller's
    tenant (an unowned/unknown brand -> **404**, never a tenant leak -- same collapse as
    ``get_overview``/``trigger_measurement``). The ranking + persist run is **scheduled onto a
    background task**, never executed inline: ``run_opportunity_refresh_job`` opens its own session,
    ranks the brand's snapshot/citation gaps via ``orchestration.opportunities.build_opportunities``,
    and idempotently refreshes the open queue -- so the request returns **202** immediately and the
    caller then reads the fresh queue from ``GET /brands/{id}/opportunities``. ``tenant_id`` is taken
    from the token (``principal``), never the client.
    """
    _ensure_brand_owned(scoped, brand_id)
    background_tasks.add_task(
        run_opportunity_refresh_job, tenant_id=principal.tenant_id, brand_id=brand_id
    )
    return OpportunityRefreshAccepted(status="accepted", brand_id=brand_id)


@router.post(
    "/brands/{brand_id}/ranking/refresh",
    status_code=202,
    response_model=RankingRefreshAccepted,
)
def refresh_ranking(
    brand_id: str,
    background_tasks: BackgroundTasks,
    principal: Annotated[Principal, Depends(require_role("editor"))],
    scoped: Annotated[TenantScopedSession, Depends(scoped_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    body: RankingRefreshRequest | None = None,
) -> RankingRefreshAccepted:
    """``POST /brands/{brand_id}/ranking/refresh`` (M5) -- source ranking candidates from the
    brand's citation pool (crawl the cited URLs for content + features), train the per-engine
    ranking models, and emit recommendation reports.

    Requires ``role >= editor`` (a ``viewer`` -> **403**) and brand ownership under the caller's
    tenant (an unowned/unknown brand -> **404**, never a tenant leak -- same collapse as
    ``refresh_opportunities``). The crawl + train run is **scheduled onto a background task**, never
    executed inline: ``run_ranking_refresh_job`` opens its own session, wires the live page fetcher +
    a config-selected (offline-capable) embedder + the model backend, and drives
    ``ranking_gen.generate_ranking_reports`` out of band, so the request returns **202** immediately.

    ``engines`` comes from the (optional) body, else every API-keyed engine the runtime has
    configured. NOTE: ranking negatives are sourced cross-engine, so >=2 engines should be measured
    for the models to train (see ``ranking.sourcing``). ``tenant_id`` is taken from the token
    (``principal``), never the client. Returns what was scheduled (engines)."""
    _ensure_brand_owned(scoped, brand_id)
    req = body if body is not None else RankingRefreshRequest()
    engines = req.engines if req.engines else configured_engine_names(settings)
    background_tasks.add_task(
        run_ranking_refresh_job,
        tenant_id=principal.tenant_id,
        brand_id=brand_id,
        engines=engines,
    )
    return RankingRefreshAccepted(status="accepted", brand_id=brand_id, engines=engines)


@router.post(
    "/brands/{brand_id}/attribution/reconcile",
    status_code=202,
    response_model=AttributionReconcileAccepted,
)
def reconcile_attribution_endpoint(
    brand_id: str,
    background_tasks: BackgroundTasks,
    principal: Annotated[Principal, Depends(require_role("editor"))],
    scoped: Annotated[TenantScopedSession, Depends(scoped_session)],
    body: AttributionReconcileRequest | None = None,
) -> AttributionReconcileAccepted:
    """``POST /brands/{brand_id}/attribution/reconcile`` (W4) -- run the fuzzy attribution writers
    (direct-referral / citation-linkage / assisted) over the brand's captured sessions + leads and
    persist the ``attribution_link`` rows ``GET /brands/{id}/pipeline`` reads.

    Requires ``role >= editor`` (a ``viewer`` -> **403**) and brand ownership under the caller's
    tenant (an unowned/unknown brand -> **404**, never a tenant leak -- same collapse as
    ``trigger_measurement``/``refresh_opportunities``). The reconcile batch is **scheduled onto a
    background task**, never executed inline: ``run_attribution_reconcile_job`` opens its own
    session and runs the three writers out of band, so the request returns **202** immediately and
    the caller then re-reads ``GET /brands/{id}/pipeline`` for the refreshed attributed value.
    ``since``/``until`` come from the (optional) body, else the job's default trailing window;
    ``tenant_id`` is taken from the token (``principal``), never the client."""
    _ensure_brand_owned(scoped, brand_id)
    req = body if body is not None else AttributionReconcileRequest()
    background_tasks.add_task(
        run_attribution_reconcile_job,
        tenant_id=principal.tenant_id,
        brand_id=brand_id,
        since=req.since,
        until=req.until,
    )
    return AttributionReconcileAccepted(status="accepted", brand_id=brand_id)
