"""Tests for the GA4 referral-reconciliation connector (M2-T12, m2-design §5).

Hermetic: `respx`-mocked HTTP (no live network), in-memory SQLite, no live GA4/AWS. Seeds tenant
``t1`` / brand ``b1`` per the task spec's failing-test snippet (`docs/tasks/M2-T12-*.md`), mirroring
`tests/attribution/integrations/test_crm.py`'s local-fixture layout.

GA4 is reconciliation-only (m2-design §5): the pixel stays system of record, so `sync` must never
touch `lead` rows -- only compute a pixel-vs-GA4 comparison per engine.
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

from gw_geo.attribution.integrations.ga4 import GA4Integration, reconcile
from gw_geo.common.config import Settings
from gw_geo.common.db import Base, Brand, Integration, Lead, Session, Tenant, TenantScopedSession

# Anchor on this file (tests/attribution/integrations/test_ga4.py -> tests/) so fixtures resolve
# regardless of the directory pytest is invoked from (mirrors tests/attribution/integrations/
# test_crm.py).
_FIXTURES_DIR = pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "ga4"


def _load_fixture(filename: str) -> dict[str, Any]:
    return json.loads((_FIXTURES_DIR / filename).read_text())


@pytest.fixture
def settings() -> Settings:
    """Test settings carrying a dummy (never-real) GA4 property id + credentials ref."""
    return Settings(ga4_property_id="properties/123456789", ga4_credentials_ref="ssm://ga4/t1")


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
    """A tenant-scoped session (tenant ``t1``) over the seeded DB -- no ``session``/``lead`` rows yet."""
    return TenantScopedSession(raw_session, "t1")


@pytest.fixture
def seeded_lead(seeded_session: TenantScopedSession) -> TenantScopedSession:
    """``seeded_session`` plus one ``Lead(email="a@x.com")`` for t1/b1, untouched by CRM sync."""
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


_RUNREPORT_URL_RE = r"https://analyticsdata\.googleapis\.com/.*:runReport"


# --- reconcile() ---------------------------------------------------------------------------------


def test_reconcile_computes_delta() -> None:
    out = reconcile({"chatgpt": 10, "perplexity": 5}, {"chatgpt": 12, "perplexity": 5})
    assert out["chatgpt"]["delta"] == 2 and out["perplexity"]["delta"] == 0


def test_reconcile_handles_asymmetric_engine_sets() -> None:
    """An engine named by only one side still gets a full record, the other side defaulting to 0."""
    out = reconcile({"chatgpt": 3}, {"perplexity": 7})
    assert out["chatgpt"] == {"pixel": 3, "ga4": 0, "delta": -3}
    assert out["perplexity"] == {"pixel": 0, "ga4": 7, "delta": 7}


def test_reconcile_empty_inputs_is_empty() -> None:
    assert reconcile({}, {}) == {}


# --- sync() ----------------------------------------------------------------------------------


@respx.mock
async def test_sync_reads_ai_referrals(
    seeded_session: TenantScopedSession, settings: Settings
) -> None:
    respx.post(url__regex=_RUNREPORT_URL_RE).mock(
        return_value=httpx.Response(
            200,
            json={
                "rows": [
                    {
                        "dimensionValues": [{"value": "perplexity.ai"}],
                        "metricValues": [{"value": "7"}],
                    }
                ]
            },
        )
    )
    integ = GA4Integration(settings, client=httpx.AsyncClient())
    n = await integ.sync(seeded_session, tenant_id="t1", brand_id="b1")
    assert n >= 1


@respx.mock
async def test_sync_sums_same_engine_across_hosts_and_filters_non_ai_sources(
    seeded_session: TenantScopedSession, settings: Settings
) -> None:
    """Fixture mixes chatgpt.com/chat.openai.com (-> one engine, summed) with non-AI sources
    (google, (direct)) that must be dropped -- only the 2 AI engines are "seen"."""
    respx.post(url__regex=_RUNREPORT_URL_RE).mock(
        return_value=httpx.Response(200, json=_load_fixture("report.json"))
    )
    integ = GA4Integration(settings, client=httpx.AsyncClient())
    n = await integ.sync(seeded_session, tenant_id="t1", brand_id="b1")
    assert n == 2


@respx.mock
async def test_sync_does_not_mutate_existing_leads(
    seeded_lead: TenantScopedSession, settings: Settings
) -> None:
    """GA4 is reconciliation-only (m2-design §5): `sync` must never touch `lead` rows."""
    respx.post(url__regex=_RUNREPORT_URL_RE).mock(
        return_value=httpx.Response(200, json=_load_fixture("report.json"))
    )
    integ = GA4Integration(settings, client=httpx.AsyncClient())
    await integ.sync(seeded_lead, tenant_id="t1", brand_id="b1")

    lead = seeded_lead.query(Lead).filter(Lead.email == "a@x.com").one()
    assert lead.crm_stage is None
    assert lead.value_usd is None
    assert seeded_lead.query(Lead).count() == 1


@respx.mock
async def test_sync_reconciles_against_pixel_session_counts(
    seeded_session: TenantScopedSession, settings: Settings
) -> None:
    """Pixel-recorded `session.engine` counts feed the pixel side of the reconciliation, not just
    the GA4 side -- seed 2 pixel-attributed "perplexity" sessions against a GA4 count of 7."""
    for _ in range(2):
        seeded_session.add(
            Session(
                id=uuid4().hex,
                tenant_id="t1",
                brand_id="b1",
                visitor_id=uuid4().hex,
                landing_url="https://acme.com/",
                referrer="https://www.perplexity.ai/",
                engine="perplexity",
                ts=datetime.now(UTC),
            )
        )
    seeded_session.commit()

    respx.post(url__regex=_RUNREPORT_URL_RE).mock(
        return_value=httpx.Response(
            200,
            json={
                "rows": [
                    {
                        "dimensionValues": [{"value": "perplexity.ai"}],
                        "metricValues": [{"value": "7"}],
                    }
                ]
            },
        )
    )
    integ = GA4Integration(settings, client=httpx.AsyncClient())
    n = await integ.sync(seeded_session, tenant_id="t1", brand_id="b1")
    assert n == 1

    out = reconcile({"perplexity": 2}, {"perplexity": 7})
    assert out["perplexity"] == {"pixel": 2, "ga4": 7, "delta": 5}


@respx.mock
async def test_sync_rejects_cross_tenant_session(settings: Settings) -> None:
    """A session scoped to a different tenant than the one requested must fail closed (TRD §7)."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    raw = SASession(engine)
    raw.add(Tenant(id="t2", name="Other", sampling_budget_daily=100.0))
    raw.commit()
    other_tenant_session = TenantScopedSession(raw, "t2")

    integ = GA4Integration(settings, client=httpx.AsyncClient())
    with pytest.raises(ValueError):
        await integ.sync(other_tenant_session, tenant_id="t1", brand_id="b1")


# --- connect() -------------------------------------------------------------------------------


def test_connect_persists_integration_row(
    seeded_session: TenantScopedSession, settings: Settings
) -> None:
    out = GA4Integration(settings).connect(
        seeded_session, tenant_id="t1", config={"credentials_ref": "ssm://ga4/t1"}
    )
    assert out["status"] in ("connected", "pending")
    assert seeded_session.query(Integration).filter_by(kind="ga4").count() == 1


def test_connect_without_ref_is_pending(
    seeded_session: TenantScopedSession, settings: Settings
) -> None:
    out = GA4Integration(settings).connect(seeded_session, tenant_id="t1", config={})
    assert out["status"] == "pending"
    row = seeded_session.query(Integration).filter_by(kind="ga4").one()
    assert row.config_ref is None
    assert row.connected_at is None


def test_connect_is_idempotent_per_tenant_and_kind(
    seeded_session: TenantScopedSession, settings: Settings
) -> None:
    """Reconnecting the same tenant/kind updates the one row rather than accumulating a new one."""
    integ = GA4Integration(settings)
    integ.connect(seeded_session, tenant_id="t1", config={})
    integ.connect(seeded_session, tenant_id="t1", config={"credentials_ref": "ssm://ga4/t1"})
    assert seeded_session.query(Integration).filter_by(kind="ga4").count() == 1
    row = seeded_session.query(Integration).filter_by(kind="ga4").one()
    assert row.status == "connected"
    assert row.config_ref == "ssm://ga4/t1"
