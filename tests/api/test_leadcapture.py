"""Self-contained tests for the public lead-capture collect endpoint (M2-T05).

Builds a minimal FastAPI app that mounts *only* the leadcapture router; it deliberately does NOT
use a shared T04 ``app_client``/conftest fixture (T04 does not exist yet). Hermetic: a single
in-memory SQLite DB (shared across the TestClient's request thread via ``StaticPool``), a
freshly-minted write-key, and dependency overrides for the DB session + pixel salt.
"""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session as SASession
from sqlalchemy.pool import StaticPool

from gw_geo.api.routers import leadcapture
from gw_geo.attribution.ingest import mint_write_key
from gw_geo.common.db import Base, Brand, Lead, Session, Tenant

SALT = "router-test-salt"


@pytest.fixture
def engine() -> Engine:
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    with SASession(eng) as s:
        s.add(Tenant(id="t1", name="Acme", sampling_budget_daily=100.0))
        s.add(Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com", competitors=[]))
        s.commit()
    return eng


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(leadcapture.router)

    def _db() -> Iterator[SASession]:
        s = SASession(engine)
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[leadcapture.get_db_session] = _db
    app.dependency_overrides[leadcapture.get_pixel_salt] = lambda: SALT
    with TestClient(app) as c:
        yield c


@pytest.fixture
def write_key() -> str:
    return mint_write_key("t1", "b1", salt=SALT)


def test_collect_is_public_and_writes(client: TestClient, write_key: str, engine: Engine) -> None:
    r = client.post(
        "/lead-capture/collect",
        json={
            "write_key": write_key,
            "type": "session",
            "visitor_id": "v1",
            "landing_url": "https://acme.com/crm",
            "referrer": "https://perplexity.ai/",
        },
    )
    assert r.status_code == 202
    assert r.json()["ok"] is True
    with SASession(engine) as s:
        row = s.query(Session).one()
        assert row.tenant_id == "t1"
        assert row.brand_id == "b1"
        assert row.visitor_id == "v1"


def test_collect_writes_lead_linked_to_session(
    client: TestClient, write_key: str, engine: Engine
) -> None:
    client.post(
        "/lead-capture/collect",
        json={
            "write_key": write_key,
            "type": "session",
            "visitor_id": "v1",
            "landing_url": "https://acme.com/crm",
        },
    )
    r = client.post(
        "/lead-capture/collect",
        json={
            "write_key": write_key,
            "type": "lead",
            "visitor_id": "v1",
            "email": "a@x.com",
            "value_usd": 500.0,
        },
    )
    assert r.status_code == 202
    with SASession(engine) as s:
        lead = s.query(Lead).one()
        session_row = s.query(Session).one()
        assert lead.email == "a@x.com"
        assert lead.tenant_id == "t1"
        assert lead.brand_id == "b1"
        assert lead.session_id == session_row.id


def test_collect_rejects_bad_key(client: TestClient) -> None:
    r = client.post(
        "/lead-capture/collect",
        json={
            "write_key": "bad",
            "type": "session",
            "visitor_id": "v",
            "landing_url": "https://a.com",
        },
    )
    assert r.status_code in (401, 403)


def test_collect_is_write_only_and_leaks_no_tenant_data(client: TestClient, write_key: str) -> None:
    r = client.post(
        "/lead-capture/collect",
        json={
            "write_key": write_key,
            "type": "session",
            "visitor_id": "v1",
            "landing_url": "https://acme.com/crm",
        },
    )
    # the beacon is write-only: the body echoes only {"ok": true}, never tenant/brand/ids
    assert r.json() == {"ok": True}


def test_collect_bad_key_response_leaks_no_tenant_data(client: TestClient) -> None:
    forged = mint_write_key("t1", "b1", salt="attacker-salt")
    r = client.post(
        "/lead-capture/collect",
        json={
            "write_key": forged,
            "type": "session",
            "visitor_id": "v1",
            "landing_url": "https://acme.com/crm",
        },
    )
    assert r.status_code in (401, 403)
    assert "t1" not in r.text
    assert "b1" not in r.text
