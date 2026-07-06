"""Content-engine endpoints (ui-spec.md §3.5/§5/§6; m3-design §3.6) -- the execution surface.

``POST /content/generate`` drafts grounded, guardrail-checked content; ``POST /content/{id}/approve``
and ``POST /content/{id}/publish`` are the **human gate** -- both require ``role >= editor``
(ui-spec §5: ``owner``/``admin``/``editor`` can approve/publish, ``viewer`` cannot), and neither can
be reached with a client-supplied draft: the id resolves the *server-side* authoritative draft, and
:func:`gw_geo.content.pipeline.ContentService.publish` refuses anything not ``APPROVED``
(``ApprovalError`` -> **409**). Nothing publishes without a passing ``GuardrailReport`` *and* an
authorized approval -- the Athena failure, made structurally impossible.

``POST /brands/{id}/kb/facts`` populates a brand's grounding corpus (RBAC ``editor``+): each fact is
embedded + upserted into the brand's per-brand :class:`~gw_geo.content.kb.KnowledgeBase`, which is
what ``/content/generate`` then grounds and claim-verifies against. This is how a brand goes from an
empty KB (generation falls back to generic, non-factual guidance) to one that can state real,
checkable facts.

The id resolution is **tenant-scoped**: ``get_asset``/``approve``/``publish`` all take
``tenant_id=principal.tenant_id`` (never a client-supplied value) and raise ``LookupError`` --
mapped to **404** by ``app.py``, same as an unowned brand -- when the id isn't found for that
tenant. A tenant-B token can therefore never approve/publish/read tenant-A's draft by guessing its
id: the 404 is indistinguishable from "doesn't exist at all". The generate + KB-ingest endpoints
likewise hydrate the target brand under the caller's own tenant (``scoped_session``), 404-ing an
unowned brand exactly like ``routers/brands.py`` -- so a tenant can only draft/ingest for its own
brands, and ``tenant_id`` is always the token's, never the request body.

:class:`ContentService` and the per-brand ``kb_factory`` are **injected** via
:func:`get_content_service` / :func:`get_kb_factory`, so the router is tested with fakes +
``app.dependency_overrides`` (no live LLM/embedding/DB call). Both defaults *raise*;
:func:`gw_geo.api.app.create_app` overrides them with the real, per-request, DB-backed providers,
and tests override them with hermetic fakes -- the same seam ``leadcapture.get_db_session`` uses.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from gw_geo.api.auth import Principal
from gw_geo.api.deps import get_current_principal, require_role, scoped_session
from gw_geo.api.schemas import (
    ContentApproveResponse,
    ContentGenerateRequest,
    ContentGenerateResponse,
    ContentPublishRequest,
    ContentPublishResponse,
    GuardrailBadges,
    KbFactIn,
    KbFactsIngested,
)
from gw_geo.common.db import Brand as BrandRow
from gw_geo.common.db import TenantScopedSession
from gw_geo.common.models import Brand, Fact
from gw_geo.content.approval import ApprovalError
from gw_geo.content.kb import KnowledgeBase
from gw_geo.content.pipeline import ContentService

router = APIRouter(tags=["content"])


def get_content_service() -> ContentService:
    """Injected :class:`ContentService` provider.

    The default **raises**: :func:`gw_geo.api.app.create_app` overrides it with a real, per-request
    provider (DB-backed store + per-brand KB + gateway-built LLM/guardrails), and tests override it
    with hermetic fakes -- both via ``app.dependency_overrides[get_content_service]``.
    """
    raise RuntimeError("content service not configured")


def get_kb_factory() -> Callable[[str], KnowledgeBase]:
    """Injected per-brand ``KnowledgeBase`` factory (``brand_id -> KnowledgeBase``).

    The default **raises**: :func:`gw_geo.api.app.create_app` overrides it with
    :func:`gw_geo.content.gateway.build_kb_factory` (from ``Settings``), and tests override it with
    an in-memory fake -- both via ``app.dependency_overrides[get_kb_factory]``.
    """
    raise RuntimeError("kb factory not configured")


def _hydrate_owned_brand(
    scoped: TenantScopedSession, brand_id: str, tenant_id: str
) -> Brand:
    """Load `brand_id` as a domain :class:`Brand`, 404-ing if it isn't the caller's tenant's.

    Mirrors ``routers/brands.py``'s ``_ensure_brand_owned``: "doesn't exist" and "belongs to another
    tenant" collapse to the same :class:`LookupError` (-> 404), never confirming a foreign brand's
    existence. ``tenant_id`` is stamped from the principal (never the DB row's own value would
    differ, but this keeps the token as the sole source of tenancy)."""
    row = scoped.query_brands().filter(BrandRow.id == brand_id).first()
    if row is None:
        raise LookupError(f"brand {brand_id!r} not found")
    return Brand(
        id=row.id,
        tenant_id=tenant_id,
        name=row.name,
        domain=row.domain,
        competitors=list(row.competitors),
    )


@router.post("/content/generate", response_model=ContentGenerateResponse)
def generate_content(
    body: ContentGenerateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    scoped: Annotated[TenantScopedSession, Depends(scoped_session)],
    svc: Annotated[ContentService, Depends(get_content_service)],
) -> ContentGenerateResponse:
    """``POST /content/generate`` (ui-spec §6) -- draft grounded content + run the guardrails.

    Hydrates the real :class:`Brand` from the DB under the caller's own tenant (an unowned/unknown
    brand -> **404**, never a tenant leak), retrieves the grounding facts from that brand's KB
    (``svc.ground``), and generates a draft grounded in exactly those facts. Returns
    ``{content_id, draft, guardrails:{claims_ok, originality_ok}}`` (ui-spec §6 verbatim). The draft
    is scoped to the caller's own tenant (``principal.tenant_id``, never the body). No RBAC gate on
    drafting itself -- the human gate is at approve/publish.
    """
    brand = _hydrate_owned_brand(scoped, body.brand_id, principal.tenant_id)
    facts = svc.ground(brand_id=brand.id, prompt_text=body.prompt_text)
    draft, report = svc.generate(
        brand=brand,
        prompt_text=body.prompt_text,
        facts=facts,
        feature_profile=None,
        target_engine=body.target_engine,
    )
    return ContentGenerateResponse(
        content_id=draft.id,
        draft=draft,
        guardrails=GuardrailBadges(
            claims_ok=report.claims_ok, originality_ok=report.originality_ok
        ),
    )


@router.post("/content/{content_id}/approve", response_model=ContentApproveResponse)
def approve_content(
    content_id: str,
    principal: Annotated[Principal, Depends(require_role("editor"))],
    svc: Annotated[ContentService, Depends(get_content_service)],
) -> ContentApproveResponse:
    """``POST /content/{id}/approve`` (ui-spec §6) -- the human approval gate.

    Requires ``role >= editor`` (a ``viewer`` -> **403**). Resolves the authoritative draft + its
    guardrail report by id, scoped to the caller's own tenant (unknown id, *or* an id belonging to
    another tenant -> **404**, never a tenant leak), then runs the T17 gate: approval is refused
    (``ApprovalError`` -> **409**) unless the report passed *and* the role is authorized. Returns
    ``{status}``.
    """
    draft, report = svc.get_asset(tenant_id=principal.tenant_id, content_id=content_id)
    try:
        approved = svc.approve(
            draft, report=report, role=principal.role, tenant_id=principal.tenant_id
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ContentApproveResponse(status=approved.status.value)


@router.post("/content/{content_id}/publish", response_model=ContentPublishResponse)
async def publish_content(
    content_id: str,
    body: ContentPublishRequest,
    principal: Annotated[Principal, Depends(require_role("editor"))],
    svc: Annotated[ContentService, Depends(get_content_service)],
) -> ContentPublishResponse:
    """``POST /content/{id}/publish`` (ui-spec §6) -- publish an approved draft via a connector.

    Requires ``role >= editor`` (a ``viewer`` -> **403**). Resolves the authoritative draft by id,
    scoped to the caller's own tenant (unknown id, *or* an id belonging to another tenant ->
    **404**, never a tenant leak); the service's ``ensure_publishable`` runs before any connector is
    touched, so an unapproved draft is refused (``ApprovalError`` -> **409**) -- the gate holds even
    for an authorized role. Returns ``{status, published_url}``.
    """
    draft, _ = svc.get_asset(tenant_id=principal.tenant_id, content_id=content_id)
    try:
        result = await svc.publish(draft, connector=body.connector, tenant_id=principal.tenant_id)
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ContentPublishResponse(status="published", published_url=result.published_url)


@router.post(
    "/brands/{brand_id}/kb/facts",
    response_model=KbFactsIngested,
    dependencies=[Depends(require_role("editor"))],
)
def ingest_kb_facts(
    brand_id: str,
    body: list[KbFactIn],
    scoped: Annotated[TenantScopedSession, Depends(scoped_session)],
    kb_factory: Annotated[Callable[[str], KnowledgeBase], Depends(get_kb_factory)],
) -> KbFactsIngested:
    """``POST /brands/{id}/kb/facts`` -- populate a brand's grounding corpus (RBAC ``editor``+).

    Body is a list of ``{text, category?, source?}``. Each is embedded + upserted into the brand's
    per-brand KB as a :class:`Fact` with a server-assigned id and ``brand_id`` (never client-set, so
    a caller can't write into another brand's corpus). Requires ``role >= editor`` (a ``viewer`` ->
    **403**, via the route dependency) and brand ownership under the caller's tenant (an
    unowned/unknown brand -> **404**). Returns ``{added}`` -- the number of facts ingested.
    """
    # Ownership check (mirrors routers/brands.py); RBAC (editor+) enforced by the route dependency.
    if scoped.query_brands().filter(BrandRow.id == brand_id).first() is None:
        raise LookupError(f"brand {brand_id!r} not found")
    kb = kb_factory(brand_id)
    for item in body:
        kb.add_fact(
            Fact(
                id=uuid4().hex,
                brand_id=brand_id,
                text=item.text,
                category=item.category,
                source=item.source,
            )
        )
    return KbFactsIngested(added=len(body))
