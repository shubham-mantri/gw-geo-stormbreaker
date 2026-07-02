"""Public lead-capture beacon router (m2-design §3, §6): ``POST /lead-capture/collect``.

This is the only *public* (unauthenticated) endpoint in the API: the pixel SDK beacons pageviews
and leads here. It is authorized not by a JWT but by a per-brand *write-key* (see
:mod:`gw_geo.attribution.ingest`), which resolves server-side to ``(tenant_id, brand_id)``. The
endpoint is strictly **write-only**: it never returns tenant data (success is a bare
``{"ok": true}``), and a bad key is rejected with a generic ``401`` that leaks nothing.

Wiring is injected via two dependencies so the router mounts both into the real app (T04, which
overrides them with the app's engine + configured salt) and, in tests, into a throwaway app with a
SQLite session + a test salt. ``get_pixel_salt`` defaults to the configured ``pixel_write_key_salt``
so the router is usable without any override; ``get_db_session`` has no sensible default and must be
overridden by whoever mounts the router.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session as SASession

from gw_geo.attribution.ingest import (
    BadWriteKey,
    LeadEvent,
    SessionEvent,
    ingest_lead,
    ingest_session,
    resolve_write_key,
)
from gw_geo.common.config import get_settings
from gw_geo.common.db import TenantScopedSession

router = APIRouter(tags=["lead-capture"])


def get_db_session() -> Iterator[SASession]:
    """Unscoped DB-session provider; must be overridden by app wiring (T04) / tests.

    Left deliberately unimplemented: the leadcapture endpoint is public and pre-tenant, so it needs
    an *unscoped* session (unlike authed routes, which use the tenant-scoped ``scoped_session``
    dependency). Whoever mounts this router supplies the real session factory.
    """
    raise NotImplementedError(
        "leadcapture.get_db_session must be overridden via app.dependency_overrides"
    )
    yield  # pragma: no cover - marks this a generator dependency for FastAPI


def get_pixel_salt() -> str:
    """HMAC salt used to verify pixel write-keys; defaults to configured ``pixel_write_key_salt``."""
    return get_settings().pixel_write_key_salt


class CollectBody(BaseModel):
    """The beacon payload. ``type`` selects which event is ingested; extra fields are per-type."""

    write_key: str
    type: Literal["session", "lead"]
    visitor_id: str
    # session fields
    landing_url: str | None = None
    referrer: str | None = None
    utm: dict[str, str] = Field(default_factory=dict)
    user_agent: str | None = None
    # lead fields
    email: str | None = None
    value_usd: float | None = None
    crm_stage: str | None = None
    self_reported_source: str | None = None
    # optional client timestamp; server fills in if absent
    ts: datetime | None = None

    @model_validator(mode="after")
    def _require_landing_url_for_session(self) -> CollectBody:
        if self.type == "session" and not self.landing_url:
            raise ValueError("landing_url is required for session events")
        return self


@router.post("/lead-capture/collect", status_code=status.HTTP_202_ACCEPTED)
def collect(
    body: CollectBody,
    session: Annotated[SASession, Depends(get_db_session)],
    salt: Annotated[str, Depends(get_pixel_salt)],
) -> dict[str, bool]:
    """Public beacon: resolve the write-key, then ingest a session or lead. Write-only."""
    try:
        tenant_id, brand_id = resolve_write_key(session, body.write_key, salt=salt)
    except BadWriteKey as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid write key"
        ) from exc

    scoped = TenantScopedSession(session, tenant_id)
    ts = body.ts or datetime.now(UTC)
    if body.type == "session":
        ingest_session(
            scoped,
            SessionEvent(
                tenant_id=tenant_id,
                brand_id=brand_id,
                visitor_id=body.visitor_id,
                landing_url=body.landing_url or "",
                referrer=body.referrer,
                utm=body.utm,
                user_agent=body.user_agent,
                ts=ts,
            ),
        )
    else:
        ingest_lead(
            scoped,
            LeadEvent(
                tenant_id=tenant_id,
                brand_id=brand_id,
                visitor_id=body.visitor_id,
                email=body.email,
                value_usd=body.value_usd,
                crm_stage=body.crm_stage,
                self_reported_source=body.self_reported_source,
                ts=ts,
            ),
        )
    return {"ok": True}
