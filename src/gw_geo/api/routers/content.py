"""Content-engine endpoints (ui-spec.md §3.5/§5/§6; m3-design §3.6) -- the execution surface.

``POST /content/generate`` drafts grounded, guardrail-checked content; ``POST /content/{id}/approve``
and ``POST /content/{id}/publish`` are the **human gate** -- both require ``role >= editor``
(ui-spec §5: ``owner``/``admin``/``editor`` can approve/publish, ``viewer`` cannot), and neither can
be reached with a client-supplied draft: the id resolves the *server-side* authoritative draft, and
:func:`gw_geo.content.pipeline.ContentService.publish` refuses anything not ``APPROVED``
(``ApprovalError`` -> **409**). Nothing publishes without a passing ``GuardrailReport`` *and* an
authorized approval -- the Athena failure, made structurally impossible.

The id resolution is also **tenant-scoped**: ``get_asset``/``approve``/``publish`` all take
``tenant_id=principal.tenant_id`` (never a client-supplied value) and raise ``LookupError`` --
mapped to **404** by ``app.py``, same as an unowned brand -- when the id isn't found for that
tenant. A tenant-B token can therefore never approve/publish/read tenant-A's draft by guessing its
id: the 404 is indistinguishable from "doesn't exist at all", so it doesn't even confirm the id
exists under another tenant.

The :class:`ContentService` is **injected** via :func:`get_content_service` so the router is tested
with a stub + ``app.dependency_overrides`` (no live LLM/guardrail/connector call), mirroring how the
rest of the M2 API injects its collaborators. The default :func:`get_content_service` *raises*: the
real, app-level construction of a ``ContentService`` is a follow-on (like M2's SecretProvider
gaps). It is deliberately not wired in :func:`create_app` because it would require per-request,
per-brand construction (the KB is brand-scoped) plus real corpus/claim/voice/connector backends and
config -- none of which can be built lazily at app-construction time without I/O. Until that lands,
``create_app`` mounts the router but leaves ``get_content_service`` raising; every caller overrides
it (production wiring will register a real provider the same way).

Tenant is always taken from the bearer token's :class:`Principal` (never the request body), so a
generated draft is scoped to the caller's own tenant by construction. Full brand-ownership
validation + KB grounding + ranking-profile shaping on ``generate`` are part of that same
real-wiring follow-on; today ``generate`` binds the draft to ``principal.tenant_id`` and passes the
prompt through to the injected service.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from gw_geo.api.auth import Principal
from gw_geo.api.deps import get_current_principal, require_role
from gw_geo.api.schemas import (
    ContentApproveResponse,
    ContentGenerateRequest,
    ContentGenerateResponse,
    ContentPublishRequest,
    ContentPublishResponse,
    GuardrailBadges,
)
from gw_geo.common.models import Brand
from gw_geo.content.approval import ApprovalError
from gw_geo.content.pipeline import ContentService

router = APIRouter(tags=["content"])


def get_content_service() -> ContentService:
    """Injected :class:`ContentService` provider.

    The default **raises**: real app-level wiring is a follow-on (see the module docstring). Tests
    (and real production wiring) override this via ``app.dependency_overrides[get_content_service]``.
    """
    raise RuntimeError("content service not configured")


@router.post("/content/generate", response_model=ContentGenerateResponse)
def generate_content(
    body: ContentGenerateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    svc: Annotated[ContentService, Depends(get_content_service)],
) -> ContentGenerateResponse:
    """``POST /content/generate`` (ui-spec §6) -- draft grounded content + run the guardrails.

    Returns ``{content_id, draft, guardrails:{claims_ok, originality_ok}}`` (ui-spec §6 verbatim).
    The draft is scoped to the caller's own tenant (``principal.tenant_id``, never the body). No
    RBAC gate on drafting itself -- the human gate is at approve/publish.
    """
    # Brand name/domain are placeholders here (cosmetic in the generation prompt); real brand
    # hydration + ownership validation + KB grounding are the real-wiring follow-on (see module
    # docstring). The security-critical field, ``tenant_id``, comes from the token, never the body.
    brand = Brand(id=body.brand_id, tenant_id=principal.tenant_id, name=body.brand_id, domain="")
    draft, report = svc.generate(
        brand=brand,
        prompt_text=body.prompt_text,
        facts=[],
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
