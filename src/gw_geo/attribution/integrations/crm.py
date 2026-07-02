"""HubSpot + Salesforce CRM connectors (m2-design §5, docs/tasks/M2-T11).

Each connector implements the `Integration` protocol (`base.py`) twice over: `connect` persists
connection state into the `integration` table, `sync` pulls deals/opportunities and enriches
`lead` rows with the CRM's deal stage + value, so the pipeline view (m2-design §2.6) reports real
revenue rather than raw lead counts.

Every HTTP call is a plain `httpx` request against an injectable `httpx.AsyncClient` -- no vendor
SDK, consistent with every M0/M1 engine adapter (`measurement/probe/perplexity.py` etc.) -- so the
default test suite never touches the network (`respx`-mocked in `tests/attribution/integrations/
test_crm.py`).

**Secrets.** The bearer token used by `sync` is read straight from `Settings`
(`hubspot_client_secret` / `salesforce_client_secret`), exactly like every other engine adapter's
`api_key`: those fields are populated from the environment (ultimately SSM-backed at deploy time,
TRD §7) and are never hardcoded here. `connect` is a separate, secret-free concern -- it only ever
extracts a `config_ref` *pointer* (e.g. an SSM path) out of the caller-supplied `config` dict and
persists that pointer (plus a status) into the `integration` table; it never inspects, let alone
stores, a raw credential. Resolving a `config_ref` pointer into live per-tenant secret material at
sync time is the same `SecretProvider` gap `common/wiring.py` already flags as not yet implemented
in this repo, so it is out of scope here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

from gw_geo.common.config import Settings
from gw_geo.common.db import Integration, Lead, TenantScopedSession

_HUBSPOT_DEALS_URL = "https://api.hubapi.com/crm/v3/objects/deals"

# Salesforce has no fixed API host (it's per-org), so this points at the login host's REST/SOQL
# query endpoint -- illustrative of the real shape (SOQL relationship traversal for the deal's
# contact email), not a claim about a specific org's instance URL. `Contact.Email` is a real SOQL
# parent-relationship field path.
_SALESFORCE_QUERY_URL = "https://login.salesforce.com/services/data/v59.0/query"
_SALESFORCE_OPPORTUNITY_SOQL = "SELECT StageName, Amount, Contact.Email FROM Opportunity"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_same_tenant(session: TenantScopedSession, tenant_id: str) -> None:
    """Raise `ValueError` if `session` is not scoped to `tenant_id` (TRD §7 fail-closed guard)."""
    if session.tenant_id != tenant_id:
        raise ValueError(
            f"session is scoped to tenant_id={session.tenant_id!r}, not {tenant_id!r}"
        )


def _enrich_matching_leads(
    session: TenantScopedSession,
    *,
    brand_id: str,
    email: str | None,
    crm_stage: str | None,
    value_usd: float | None,
) -> set[str]:
    """Set `crm_stage`/`value_usd` on every tenant-scoped `lead` matching `(brand_id, email)`.

    A no-op returning the empty set when `email` is falsy (an upstream deal with no matched
    contact email enriches nothing). Returns the touched lead ids so callers can dedupe an
    enriched-lead count across multiple upstream deals that happen to resolve to the same lead.
    """
    if not email:
        return set()
    leads = session.query(Lead).filter(Lead.brand_id == brand_id, Lead.email == email).all()
    for lead in leads:
        lead.crm_stage = crm_stage
        lead.value_usd = value_usd
    return {lead.id for lead in leads}


def _connect(
    session: TenantScopedSession, *, tenant_id: str, kind: str, config: dict[str, Any]
) -> dict[str, str]:
    """Shared `connect` body for every CRM connector (`kind` distinguishes hubspot/salesforce).

    Upserts the single `integration` row for `(tenant_id, kind)` rather than accumulating a new
    row per reconnect -- connection state is a single fact about a tenant's CRM link. Only ever
    reads the `access_token_ref` key out of `config`; any other key a caller passes (e.g. a raw
    token under a different key, by mistake) is never inspected, so it cannot reach the database.
    """
    _require_same_tenant(session, tenant_id)
    config_ref = config.get("access_token_ref")
    status = "connected" if config_ref else "pending"

    row = session.query(Integration).filter(Integration.kind == kind).first()
    if row is None:
        row = Integration(
            id=uuid4().hex,
            tenant_id=tenant_id,
            kind=kind,
            status=status,
            config_ref=config_ref,
            connected_at=_utcnow() if status == "connected" else None,
        )
        session.add(row)
    else:
        row.status = status
        row.config_ref = config_ref
        if status == "connected":
            row.connected_at = _utcnow()
    session.commit()
    return {"status": row.status}


class HubSpotIntegration:
    """`Integration` for HubSpot: deal stage + amount -> `Lead.crm_stage` / `Lead.value_usd`."""

    kind = "hubspot"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client if client is not None else httpx.AsyncClient()

    def connect(
        self, session: TenantScopedSession, *, tenant_id: str, config: dict[str, Any]
    ) -> dict[str, str]:
        return _connect(session, tenant_id=tenant_id, kind=self.kind, config=config)

    async def sync(self, session: TenantScopedSession, *, tenant_id: str, brand_id: str) -> int:
        """GET HubSpot deals and enrich matching `lead` rows by contact email.

        Returns the number of distinct `lead` rows enriched.
        """
        _require_same_tenant(session, tenant_id)
        response = await self._client.get(
            _HUBSPOT_DEALS_URL,
            headers={"Authorization": f"Bearer {self._settings.hubspot_client_secret}"},
            params={"properties": "dealstage,amount,email"},
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()

        touched: set[str] = set()
        for deal in payload.get("results", []):
            properties: dict[str, Any] = deal.get("properties", {})
            amount = properties.get("amount")
            touched |= _enrich_matching_leads(
                session,
                brand_id=brand_id,
                email=properties.get("email"),
                crm_stage=properties.get("dealstage"),
                value_usd=float(amount) if amount is not None else None,
            )
        session.commit()
        return len(touched)


class SalesforceIntegration:
    """`Integration` for Salesforce: Opportunity stage + amount -> `Lead.crm_stage`/`value_usd`."""

    kind = "salesforce"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client if client is not None else httpx.AsyncClient()

    def connect(
        self, session: TenantScopedSession, *, tenant_id: str, config: dict[str, Any]
    ) -> dict[str, str]:
        return _connect(session, tenant_id=tenant_id, kind=self.kind, config=config)

    async def sync(self, session: TenantScopedSession, *, tenant_id: str, brand_id: str) -> int:
        """SOQL-query Salesforce Opportunities and enrich matching `lead` rows by contact email.

        Returns the number of distinct `lead` rows enriched.
        """
        _require_same_tenant(session, tenant_id)
        response = await self._client.get(
            _SALESFORCE_QUERY_URL,
            headers={"Authorization": f"Bearer {self._settings.salesforce_client_secret}"},
            params={"q": _SALESFORCE_OPPORTUNITY_SOQL},
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()

        touched: set[str] = set()
        for record in payload.get("records", []):
            contact: dict[str, Any] = record.get("Contact") or {}
            amount = record.get("Amount")
            touched |= _enrich_matching_leads(
                session,
                brand_id=brand_id,
                email=contact.get("Email"),
                crm_stage=record.get("StageName"),
                value_usd=float(amount) if amount is not None else None,
            )
        session.commit()
        return len(touched)
