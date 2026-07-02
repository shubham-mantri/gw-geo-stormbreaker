"""Opportunities queue endpoints (ui-spec.md §3.4/§6; m3-design.md §4/§5) -- the bridge from
insight to action: list a brand's ranked gaps, and the **`act` → content** flow ("Fix this ▸")
that spawns a pre-scoped draft via the content pipeline (T22).

The :class:`OpportunityService` is **injected** via :func:`get_opportunity_service`, mirroring
``routers/content.py``'s ``get_content_service`` seam exactly: the router is tested with a stub +
``app.dependency_overrides`` (no live DB/ranking/LLM call). The default
:func:`get_opportunity_service` *raises*: composing T19's ``orchestration.opportunities.
build_opportunities`` (which needs brand-scoped snapshots, ranking reports, and a citation source
mix, all loaded from the DB) with the T22 content pipeline (whose ``ContentService`` needs a
brand's KB/corpus/claim/voice/connector backends) into one app-constructible service -- plus
persisting `Opportunity` rows so an id handed out by ``list_for_brand`` is still resolvable by a
later ``act`` call -- is a follow-on, exactly like T22 left ``ContentService``'s own real
construction as a follow-on (see that module's docstring). Until that lands, ``create_app`` mounts
this router but leaves ``get_opportunity_service`` raising; every caller (tests, and eventually
production wiring) overrides it the same way.

Tenant is always taken from the bearer token's :class:`Principal` (never the request body/path), so
both the list and the act flow are scoped to the caller's own tenant by construction.
"""

from __future__ import annotations

from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends

from gw_geo.api.auth import Principal
from gw_geo.api.deps import get_current_principal, require_role
from gw_geo.api.schemas import OpportunityActResponse, OpportunityOut

router = APIRouter(tags=["opportunities"])


class OpportunityService(Protocol):
    """Collaborator T21 injects: rank a brand's opportunities, and act on one to spawn content."""

    def list_for_brand(self, *, tenant_id: str, brand_id: str) -> list[dict[str, Any]]:
        """Return this tenant's ranked opportunities for `brand_id` (ui-spec §6 row shape)."""
        ...

    def act(self, *, tenant_id: str, opportunity_id: str) -> dict[str, Any]:
        """Spawn a pre-scoped content draft from `opportunity_id`; return `{"content_id": ...}`."""
        ...


def get_opportunity_service() -> OpportunityService:
    """Injected :class:`OpportunityService` provider.

    The default **raises**: real app-level wiring is a follow-on (see the module docstring). Tests
    (and real production wiring) override this via
    ``app.dependency_overrides[get_opportunity_service]``.
    """
    raise RuntimeError("opportunity service not configured")


@router.get("/brands/{brand_id}/opportunities", response_model=list[OpportunityOut])
def list_opportunities(
    brand_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    svc: Annotated[OpportunityService, Depends(get_opportunity_service)],
) -> list[OpportunityOut]:
    """``GET /brands/{brand_id}/opportunities`` (ui-spec §3.4/§6) -- ranked gaps for the brand.

    Returns ``[{id,title,rationale,est_impact,engine}]`` (ui-spec §6 verbatim). No RBAC gate
    beyond authentication (ui-spec §5: only approve/publish-style actions require ``editor``+) --
    tenant is the caller's own (``principal.tenant_id``), never the client-supplied path/body.
    """
    rows = svc.list_for_brand(tenant_id=principal.tenant_id, brand_id=brand_id)
    return [OpportunityOut(**row) for row in rows]


@router.post("/opportunities/{opportunity_id}/act", response_model=OpportunityActResponse)
def act_on_opportunity(
    opportunity_id: str,
    principal: Annotated[Principal, Depends(require_role("editor"))],
    svc: Annotated[OpportunityService, Depends(get_opportunity_service)],
) -> OpportunityActResponse:
    """``POST /opportunities/{opportunity_id}/act`` (ui-spec §3.4/§6) -- "Fix this ▸".

    Requires ``role >= editor`` (a ``viewer`` -> **403**), matching the RBAC posture of the
    content-workspace write actions this delegates into. Delegates to ``service.act(...)``, which
    spawns a pre-scoped draft via the content pipeline (T22) and returns ``{content_id}`` (ui-spec
    §6 verbatim). Tenant is the caller's own (``principal.tenant_id``), never the client.
    """
    result = svc.act(tenant_id=principal.tenant_id, opportunity_id=opportunity_id)
    return OpportunityActResponse(content_id=result["content_id"])
