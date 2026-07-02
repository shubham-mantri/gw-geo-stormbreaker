"""Lead-capture ingestion substrate (m2-design §2.1, §6; TRD §6.1).

The lead-capture pixel beacons land here. This module owns three things:

* the wire events (:class:`SessionEvent`, :class:`LeadEvent`) the beacon carries;
* tenant-scoped persistence of ``session`` / ``lead`` rows (:func:`ingest_session`,
  :func:`ingest_lead`), with a lead linked to the visitor's most-recent session; and
* the write-key scheme (:func:`mint_write_key`, :func:`resolve_write_key`) that maps a public,
  per-brand, *write-only* key to its ``(tenant_id, brand_id)`` server-side.

Ingestion always runs through a :class:`~gw_geo.common.db.TenantScopedSession`, so a beacon can only
ever write into the brand its key resolves to. Write-key *resolution*, by contrast, runs against the
raw pre-tenant session because the key is what *establishes* the tenant in the first place.

**Write-key scheme.** A key is a signed, self-describing token
``gwk_<b64url(tenant:brand)>.<hmac_sha256(salt, tenant:brand)>``. The HMAC (keyed by the
server-only ``pixel_write_key_salt``) is the security boundary: without the salt a holder cannot
forge a key for any *other* brand or tenant, so a leaked key is write-only and confined to its own
brand (m2-design §6, §12). The tenant/brand ids embedded in the token are internal identifiers, not
secrets (the pixel snippet is public client-side JS anyway); resolution additionally verifies the
brand still exists under that tenant, so a key for a deleted brand stops resolving.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as SASession

from gw_geo.common.db import Brand, Lead
from gw_geo.common.db import Session as SessionRow
from gw_geo.common.db import TenantScopedSession

_KEY_PREFIX = "gwk_"


class BadWriteKey(ValueError):
    """Raised when a write-key is malformed, unsigned/missigned, or resolves to no known brand.

    A :class:`ValueError` subclass; the router translates it into a ``401``/``403`` with a generic
    detail so nothing about the failure mode (or any tenant) leaks to the caller.
    """


class SessionEvent(BaseModel):
    """A beaconed pageview (m2-design §2.1). One row per visit; the origin of referral data."""

    tenant_id: str
    brand_id: str
    visitor_id: str
    landing_url: str
    referrer: str | None = None
    utm: dict[str, str] = Field(default_factory=dict)
    user_agent: str | None = None
    ts: datetime


class LeadEvent(BaseModel):
    """A captured lead (m2-design §2.1). Optional fields fill in over the lead lifecycle."""

    tenant_id: str
    brand_id: str
    visitor_id: str
    email: str | None = None
    value_usd: float | None = None
    crm_stage: str | None = None
    self_reported_source: str | None = None
    ts: datetime


def ingest_session(session: TenantScopedSession, ev: SessionEvent) -> str:
    """Persist a tenant-scoped ``session`` row for a beaconed pageview; return its id.

    ``engine`` is left unset -- referrer/UTM classification is a downstream mechanism (T06,
    ``attribution/referral.py``), not ingestion. The scoped session's ``add`` rejects any event
    whose ``tenant_id`` differs from the session's tenant.
    """
    row = SessionRow(
        id=uuid4().hex,
        tenant_id=ev.tenant_id,
        brand_id=ev.brand_id,
        visitor_id=ev.visitor_id,
        landing_url=ev.landing_url,
        referrer=ev.referrer,
        utm=ev.utm,
        engine=None,
        user_agent=ev.user_agent,
        ts=ev.ts,
    )
    session.add(row)
    session.commit()
    return row.id


def ingest_lead(session: TenantScopedSession, ev: LeadEvent) -> str:
    """Persist a tenant-scoped ``lead`` row; return its id.

    Links the lead to the visitor's most-recent ``session`` for the same brand (by ``ts``), or to
    no session if the visitor has none yet. The lookup is tenant-scoped, so it can only match
    sessions the caller's key is allowed to see.
    """
    latest = (
        session.query(SessionRow)
        .filter(SessionRow.brand_id == ev.brand_id, SessionRow.visitor_id == ev.visitor_id)
        .order_by(SessionRow.ts.desc())
        .first()
    )
    lead = Lead(
        id=uuid4().hex,
        tenant_id=ev.tenant_id,
        brand_id=ev.brand_id,
        visitor_id=ev.visitor_id,
        session_id=latest.id if latest is not None else None,
        email=ev.email,
        value_usd=ev.value_usd,
        crm_stage=ev.crm_stage,
        self_reported_source=ev.self_reported_source,
        ts=ev.ts,
    )
    session.add(lead)
    session.commit()
    return lead.id


def _sign(payload: bytes, salt: str) -> str:
    return hmac.new(salt.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def mint_write_key(tenant_id: str, brand_id: str, *, salt: str) -> str:
    """Mint the public, per-brand, write-only pixel key for ``(tenant_id, brand_id)``.

    Deterministic given the salt. Install this in the brand's pixel snippet (``GET
    /lead-capture/snippet``); it can only ever *write* this brand's sessions/leads.
    """
    payload = f"{tenant_id}:{brand_id}".encode()
    body = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{_KEY_PREFIX}{body}.{_sign(payload, salt)}"


def resolve_write_key(session: SASession, key: str, *, salt: str) -> tuple[str, str]:
    """Verify a write-key and resolve it to ``(tenant_id, brand_id)``; raise on any bad key.

    Runs against the raw (unscoped) session: resolution is what *establishes* the tenant, so it
    necessarily precedes tenant scoping. Every failure mode -- malformed token, bad signature,
    unknown brand -- raises :class:`BadWriteKey` uniformly, so the caller learns nothing beyond
    "invalid".
    """
    if not key.startswith(_KEY_PREFIX):
        raise BadWriteKey("invalid write key")
    encoded, sep, signature = key[len(_KEY_PREFIX) :].partition(".")
    if not sep or not encoded or not signature:
        raise BadWriteKey("invalid write key")
    try:
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (binascii.Error, ValueError) as exc:
        raise BadWriteKey("invalid write key") from exc
    if not hmac.compare_digest(_sign(payload, salt), signature):
        raise BadWriteKey("invalid write key")
    try:
        tenant_id, colon, brand_id = payload.decode("utf-8").partition(":")
    except UnicodeDecodeError as exc:
        raise BadWriteKey("invalid write key") from exc
    if not colon or not tenant_id or not brand_id:
        raise BadWriteKey("invalid write key")
    brand = session.get(Brand, brand_id)
    if brand is None or brand.tenant_id != tenant_id:
        raise BadWriteKey("invalid write key")
    return tenant_id, brand_id
