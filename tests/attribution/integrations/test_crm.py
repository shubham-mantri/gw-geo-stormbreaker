"""Tests for the HubSpot/Salesforce CRM connectors (M2-T11, m2-design §5).

Hermetic: `respx`-mocked HTTP (no live network), in-memory SQLite, no live SSM/AWS. Seeds tenant
``t1`` / brand ``b1`` per the task spec's failing-test snippet (`docs/tasks/M2-T11-*.md`).
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest
import respx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SASession

from gw_geo.attribution.integrations.crm import HubSpotIntegration, SalesforceIntegration
from gw_geo.common.config import Settings
from gw_geo.common.db import Base, Brand, Integration, Lead, Tenant, TenantScopedSession

# Anchor on this file (tests/attribution/integrations/test_crm.py -> tests/) so fixtures resolve
# regardless of the directory pytest is invoked from (mirrors tests/measurement/probe/fixtures.py).
_FIXTURES_DIR = pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "crm"


def _load_fixture(filename: str) -> dict[str, Any]:
    return json.loads((_FIXTURES_DIR / filename).read_text())


@pytest.fixture
def settings() -> Settings:
    """Test settings carrying dummy (never-real) CRM bearer tokens."""
    return Settings(hubspot_client_secret="test-hubspot-token", salesforce_client_secret="test-sf-token")


@pytest.fixture
def raw_session() -> Iterator[SASession]:
    """A raw SQLite session seeded with tenant ``t1`` + brand ``b1``."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = SASession(engine)
    s.add(Tenant(id="t1", name="Acme", sampling_budget_daily=100.0))
    s.add(Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com", competitors=[]))
    s.commit()
    yield s
    s.close()


@pytest.fixture
def seeded_session(raw_session: SASession) -> TenantScopedSession:
    """A tenant-scoped session (tenant ``t1``) over the seeded DB -- no ``lead`` rows yet."""
    return TenantScopedSession(raw_session, "t1")


@pytest.fixture
def seeded_lead(seeded_session: TenantScopedSession) -> TenantScopedSession:
    """``seeded_session`` plus one ``Lead(email="a@x.com")`` for t1/b1 (the task spec's fixture)."""
    seeded_session.add(
        Lead(
            id=uuid4().hex,
            tenant_id="t1",
            brand_id="b1",
            visitor_id="v1",
            email="a@x.com",
            ts=datetime.now(UTC),
        )
    )
    seeded_session.commit()
    return seeded_session


# --- HubSpot -----------------------------------------------------------------------------------


@respx.mock
async def test_sync_enriches_lead(seeded_lead: TenantScopedSession, settings: Settings) -> None:
    respx.get(url__regex=r"https://api\.hubapi\.com/crm/v3/objects/deals.*").mock(
        return_value=httpx.Response(200, json=_load_fixture("hubspot_deals.json"))
    )
    integ = HubSpotIntegration(settings, client=httpx.AsyncClient())
    n = await integ.sync(seeded_lead, tenant_id="t1", brand_id="b1")
    assert n == 1
    lead = next(lead for lead in seeded_lead.query(Lead).all() if lead.email == "a@x.com")
    assert lead.crm_stage == "closedwon" and lead.value_usd == 5000.0


@respx.mock
async def test_sync_skips_unmatched_email(
    seeded_lead: TenantScopedSession, settings: Settings
) -> None:
    """A deal whose contact email matches no local ``lead`` enriches nothing (no crash, n == 0)."""
    respx.get(url__regex=r"https://api\.hubapi\.com/crm/v3/objects/deals.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"properties": {"dealstage": "closedwon", "amount": "999", "email": "nobody@x.com"}}
                ]
            },
        )
    )
    integ = HubSpotIntegration(settings, client=httpx.AsyncClient())
    n = await integ.sync(seeded_lead, tenant_id="t1", brand_id="b1")
    assert n == 0
    lead = seeded_lead.query(Lead).filter(Lead.email == "a@x.com").one()
    assert lead.crm_stage is None and lead.value_usd is None


@respx.mock
async def test_sync_rejects_cross_tenant_session(settings: Settings) -> None:
    """A session scoped to a different tenant than the one requested must fail closed (TRD §7)."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    raw = SASession(engine)
    raw.add(Tenant(id="t2", name="Other", sampling_budget_daily=100.0))
    raw.commit()
    other_tenant_session = TenantScopedSession(raw, "t2")

    integ = HubSpotIntegration(settings, client=httpx.AsyncClient())
    with pytest.raises(ValueError):
        await integ.sync(other_tenant_session, tenant_id="t1", brand_id="b1")


def test_connect_persists_integration_row(
    seeded_session: TenantScopedSession, settings: Settings
) -> None:
    out = HubSpotIntegration(settings).connect(
        seeded_session, tenant_id="t1", config={"access_token_ref": "ssm://hubspot/t1"}
    )
    assert out["status"] in ("connected", "pending")
    assert seeded_session.query(Integration).filter_by(kind="hubspot").count() == 1


def test_connect_without_ref_is_pending(
    seeded_session: TenantScopedSession, settings: Settings
) -> None:
    out = HubSpotIntegration(settings).connect(seeded_session, tenant_id="t1", config={})
    assert out["status"] == "pending"
    row = seeded_session.query(Integration).filter_by(kind="hubspot").one()
    assert row.config_ref is None
    assert row.connected_at is None


def test_connect_never_persists_raw_secret(
    seeded_session: TenantScopedSession, settings: Settings
) -> None:
    """A raw credential slipped into ``config`` under any key other than the ref is never stored."""
    out = HubSpotIntegration(settings).connect(
        seeded_session,
        tenant_id="t1",
        config={"access_token_ref": "ssm://hubspot/t1", "access_token": "raw-secret-do-not-store"},
    )
    assert out["status"] == "connected"
    row = seeded_session.query(Integration).filter_by(kind="hubspot").one()
    assert row.config_ref == "ssm://hubspot/t1"
    assert row.config_ref is not None and "raw-secret-do-not-store" not in row.config_ref


def test_connect_is_idempotent_per_tenant_and_kind(
    seeded_session: TenantScopedSession, settings: Settings
) -> None:
    """Reconnecting the same tenant/kind updates the one row rather than accumulating a new one."""
    integ = HubSpotIntegration(settings)
    integ.connect(seeded_session, tenant_id="t1", config={})
    integ.connect(seeded_session, tenant_id="t1", config={"access_token_ref": "ssm://hubspot/t1"})
    assert seeded_session.query(Integration).filter_by(kind="hubspot").count() == 1
    row = seeded_session.query(Integration).filter_by(kind="hubspot").one()
    assert row.status == "connected"
    assert row.config_ref == "ssm://hubspot/t1"


# --- Salesforce ----------------------------------------------------------------------------------


@respx.mock
async def test_sync_salesforce_enriches_lead(
    seeded_lead: TenantScopedSession, settings: Settings
) -> None:
    respx.get("https://login.salesforce.com/services/data/v59.0/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "records": [
                    {
                        "StageName": "Closed Won",
                        "Amount": 7500,
                        "Contact": {"Email": "a@x.com"},
                    }
                ]
            },
        )
    )
    integ = SalesforceIntegration(settings, client=httpx.AsyncClient())
    n = await integ.sync(seeded_lead, tenant_id="t1", brand_id="b1")
    assert n == 1
    lead = seeded_lead.query(Lead).filter(Lead.email == "a@x.com").one()
    assert lead.crm_stage == "Closed Won" and lead.value_usd == 7500.0


def test_connect_persists_integration_row_salesforce(
    seeded_session: TenantScopedSession, settings: Settings
) -> None:
    out = SalesforceIntegration(settings).connect(
        seeded_session, tenant_id="t1", config={"access_token_ref": "ssm://salesforce/t1"}
    )
    assert out["status"] in ("connected", "pending")
    assert seeded_session.query(Integration).filter_by(kind="salesforce").count() == 1
    # hubspot and salesforce connections are independent rows, both scoped to tenant t1.
    assert seeded_session.query(Integration).count() == 1
