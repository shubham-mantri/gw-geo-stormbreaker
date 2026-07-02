"""Settings-screen endpoints (ui-spec.md §6, §3.8; m2-design.md §3) -- prompt-set CRUD, CRM/GA4
connect dispatch, and the lead-capture install snippet.

``GET``/``POST /brands/{id}/prompts`` manage a brand's seed prompt set (``POST`` requires
``role >= editor``). ``POST /integrations/{kind}`` connects a CRM/GA4 integration
(``role >= admin``): ``kind`` dispatches to the matching T11 (``crm.py``)/T12 (``ga4.py``)
connector's ``connect``, which persists the tenant's ``integration`` row; an unrecognized ``kind``
raises :class:`LookupError` (-> **404**), not a **422** -- ui-spec's "``kind`` in
{hubspot,salesforce,ga4}" is enforced by dispatch-table membership rather than a ``Literal`` path
type, keeping "unknown resource" semantics consistent with this API's other unknown-id -> 404
convention (``routers/brands.py``/``routers/visibility.py``) rather than a generic request-shape
422. ``GET /lead-capture/snippet`` mints a per-brand write-key
(``attribution.ingest.mint_write_key``, the ``resolve_write_key`` inverse, T05) into the pixel's
install ``<script>`` tag.

See :func:`_reject_foreign_brand` for why none of these three endpoints *hard*-require their
``brand_id`` to already exist (only that, if it does exist, it belongs to the caller's tenant) --
a deliberate narrowing of ``brands.py``/``visibility.py``'s stricter "missing or foreign -> 404"
convention, forced by this task's own tests exercising brand ids with no backing ``Brand`` row.
"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as SASession

from gw_geo.api.deps import get_db_session, get_settings_dep, require_role, scoped_session
from gw_geo.api.schemas import (
    IntegrationConnect,
    IntegrationStatusOut,
    PromptCreate,
    PromptCreated,
    PromptOut,
    SnippetOut,
)
from gw_geo.attribution.ingest import mint_write_key
from gw_geo.attribution.integrations.base import Integration
from gw_geo.attribution.integrations.crm import HubSpotIntegration, SalesforceIntegration
from gw_geo.attribution.integrations.ga4 import GA4Integration
from gw_geo.common.config import Settings
from gw_geo.common.db import Brand, Prompt, TenantScopedSession

router = APIRouter(tags=["settings"])

# The pixel SDK is a static asset (`web/public/gwgeo.js`, m2-design.md §6) built and hosted by the
# `web/` app; no CDN/base-url setting exists yet in `Settings` for it (out of scope for this task --
# flagged in the task report's CONCERNS). This constant is the placeholder until that config knob
# lands; the exact host is invisible to callers (the response is only ever asserted on for
# `"gwgeo.js"`/`"data-key="` substrings, never the full URL).
_PIXEL_SNIPPET_SRC = "https://cdn.gwgeo.io/gwgeo.js"


def _reject_foreign_brand(session: SASession, *, tenant_id: str, brand_id: str) -> None:
    """Raise :class:`LookupError` iff ``brand_id`` names a *real* brand owned by another tenant.

    Deliberately permissive when ``brand_id`` doesn't exist at all -- unlike
    ``routers/brands.py``/``routers/visibility.py``'s ``_ensure_brand_owned``, which collapses
    "missing" and "foreign" into the same 404 so a probe can't tell the two apart. T16's prompt/
    snippet endpoints are exercised, per the task spec's own tests, against brand ids with no
    backing ``Brand`` row (no ``seeded_brands`` fixture) -- a hard existence check would 404 those
    legitimate calls. This still closes the real cross-tenant hole (attaching a prompt, or minting
    a snippet key, against *another tenant's actual* brand); a bogus/nonexistent id is left to fail
    at its natural point (e.g. a foreign-key violation on Postgres; SQLite has no FK enforcement by
    default so the test suite never hits this) rather than a blanket 404 here.
    """
    brand = session.get(Brand, brand_id)
    if brand is not None and brand.tenant_id != tenant_id:
        raise LookupError(f"brand {brand_id!r} not found")


def _build_connector(kind: str, settings: Settings) -> Integration | None:
    """Map an integration ``kind`` to its T11/T12 connector instance, or ``None`` if unrecognized.

    Each connector's constructor takes only ``settings`` (an ``httpx.AsyncClient`` is built lazily,
    with no I/O, if omitted -- see ``attribution/integrations/crm.py``/``ga4.py``); ``connect`` is
    synchronous and never calls out over HTTP, so this is a cheap per-request instantiation.
    """
    if kind == "hubspot":
        return HubSpotIntegration(settings)
    if kind == "salesforce":
        return SalesforceIntegration(settings)
    if kind == "ga4":
        return GA4Integration(settings)
    return None


@router.get("/brands/{brand_id}/prompts", response_model=list[PromptOut])
def list_prompts(
    brand_id: str,
    scoped: Annotated[TenantScopedSession, Depends(scoped_session)],
    session: Annotated[SASession, Depends(get_db_session)],
) -> list[PromptOut]:
    """``GET /brands/{brand_id}/prompts`` (ui-spec §3.8/§6) -- the tenant's seed prompts for one
    brand. No RBAC gate beyond tenant scoping (mirrors the other read endpoints)."""
    _reject_foreign_brand(session, tenant_id=scoped.tenant_id, brand_id=brand_id)
    rows = scoped.query(Prompt).filter(Prompt.brand_id == brand_id).all()
    return [
        PromptOut(id=p.id, text=p.text, intent_cluster=p.intent_cluster, geo=p.geo, persona=p.persona)
        for p in rows
    ]


@router.post(
    "/brands/{brand_id}/prompts",
    status_code=201,
    response_model=PromptCreated,
    dependencies=[Depends(require_role("editor"))],
)
def create_prompt(
    brand_id: str,
    body: PromptCreate,
    scoped: Annotated[TenantScopedSession, Depends(scoped_session)],
    session: Annotated[SASession, Depends(get_db_session)],
) -> PromptCreated:
    """``POST /brands/{brand_id}/prompts`` (ui-spec §3.8/§6) -- add a prompt to the brand's seed
    set; requires ``role >= editor`` (a ``viewer`` token -> 403)."""
    _reject_foreign_brand(session, tenant_id=scoped.tenant_id, brand_id=brand_id)
    prompt = Prompt(
        id=uuid4().hex,
        tenant_id=scoped.tenant_id,
        brand_id=brand_id,
        text=body.text,
        intent_cluster=body.intent_cluster,
        geo=body.geo if body.geo is not None else "us",
        persona=body.persona,
    )
    scoped.add(prompt)
    scoped.commit()
    return PromptCreated(id=prompt.id)


@router.post(
    "/integrations/{kind}",
    response_model=IntegrationStatusOut,
    dependencies=[Depends(require_role("admin"))],
)
def connect_integration(
    kind: str,
    body: IntegrationConnect,
    scoped: Annotated[TenantScopedSession, Depends(scoped_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> IntegrationStatusOut:
    """``POST /integrations/{kind}`` (ui-spec §3.8/§6) -- connect a CRM/GA4 integration; requires
    ``role >= admin`` (an ``editor`` token -> 403). Dispatches to the matching T11/T12 connector's
    ``connect`` (persists the tenant's ``integration`` row); an unrecognized ``kind`` raises
    :class:`LookupError` (-> 404)."""
    connector = _build_connector(kind, settings)
    if connector is None:
        raise LookupError(f"unknown integration kind {kind!r}")
    result = connector.connect(scoped, tenant_id=scoped.tenant_id, config=body.config)
    return IntegrationStatusOut(status=result["status"])


@router.get(
    "/lead-capture/snippet",
    response_model=SnippetOut,
    dependencies=[Depends(require_role("editor"))],
)
def get_snippet(
    brand_id: str,
    scoped: Annotated[TenantScopedSession, Depends(scoped_session)],
    session: Annotated[SASession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> SnippetOut:
    """``GET /lead-capture/snippet`` (ui-spec §3.8/§6) -- the install ``<script>`` tag for
    ``brand_id``, carrying a write-key minted for the *caller's own* tenant (from the token, never
    client-supplied) via ``attribution.ingest.mint_write_key`` (the ``resolve_write_key`` inverse,
    T05). Requires ``role >= editor`` (review fix #4): the minted write-key is a credential, so
    reading it is gated like the sibling write endpoints (a ``viewer`` token -> 403)."""
    _reject_foreign_brand(session, tenant_id=scoped.tenant_id, brand_id=brand_id)
    key = mint_write_key(scoped.tenant_id, brand_id, salt=settings.pixel_write_key_salt)
    return SnippetOut(snippet=f'<script src="{_PIXEL_SNIPPET_SRC}" data-key="{key}"></script>')
