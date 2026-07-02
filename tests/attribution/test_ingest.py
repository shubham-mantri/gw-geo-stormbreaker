"""Tests for lead-capture ingestion + write-key resolution (M2-T05, m2-design §2.1/§6).

Hermetic: in-memory SQLite, no network. Seeds tenant ``t1`` + brand ``b1`` per the task spec.
Ingestion runs through a ``TenantScopedSession`` so cross-tenant writes are impossible by
construction; write-key resolution runs against the raw (pre-tenant) session because the key is
what *establishes* the tenant.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SASession

from gw_geo.attribution.ingest import (
    BadWriteKey,
    LeadEvent,
    SessionEvent,
    ingest_lead,
    ingest_session,
    mint_write_key,
    resolve_write_key,
)
from gw_geo.common.db import Base, Brand, Lead, Session, Tenant, TenantScopedSession

SALT = "unit-test-salt"


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
    """A tenant-scoped session (tenant ``t1``) over the seeded DB."""
    return TenantScopedSession(raw_session, "t1")


def test_session_then_lead_links(seeded_session: TenantScopedSession) -> None:
    # seeded_session has tenant t1 + brand b1
    sid = ingest_session(
        seeded_session,
        SessionEvent(
            tenant_id="t1",
            brand_id="b1",
            visitor_id="v1",
            landing_url="https://acme.com/crm",
            referrer="https://chatgpt.com/",
            ts=datetime.now(UTC),
        ),
    )
    lid = ingest_lead(
        seeded_session,
        LeadEvent(
            tenant_id="t1",
            brand_id="b1",
            visitor_id="v1",
            email="a@x.com",
            value_usd=1000.0,
            ts=datetime.now(UTC),
        ),
    )
    assert sid and lid
    lead = seeded_session.query(Lead).filter(Lead.id == lid).one()
    assert lead.session_id == sid  # lead links to the visitor's originating session
    assert lead.email == "a@x.com"
    assert lead.value_usd == 1000.0


def test_lead_links_to_latest_session(seeded_session: TenantScopedSession) -> None:
    base = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    older = ingest_session(
        seeded_session,
        SessionEvent(
            tenant_id="t1",
            brand_id="b1",
            visitor_id="v9",
            landing_url="https://acme.com/a",
            ts=base,
        ),
    )
    newer = ingest_session(
        seeded_session,
        SessionEvent(
            tenant_id="t1",
            brand_id="b1",
            visitor_id="v9",
            landing_url="https://acme.com/b",
            ts=base + timedelta(hours=1),
        ),
    )
    lid = ingest_lead(
        seeded_session,
        LeadEvent(
            tenant_id="t1",
            brand_id="b1",
            visitor_id="v9",
            email="z@x.com",
            ts=base + timedelta(hours=2),
        ),
    )
    lead = seeded_session.query(Lead).filter(Lead.id == lid).one()
    assert lead.session_id == newer
    assert lead.session_id != older


def test_lead_with_no_prior_session_has_null_session_id(
    seeded_session: TenantScopedSession,
) -> None:
    lid = ingest_lead(
        seeded_session,
        LeadEvent(
            tenant_id="t1",
            brand_id="b1",
            visitor_id="ghost",
            email="g@x.com",
            ts=datetime.now(UTC),
        ),
    )
    lead = seeded_session.query(Lead).filter(Lead.id == lid).one()
    assert lead.session_id is None


def test_ingest_session_persists_referrer_and_utm(
    seeded_session: TenantScopedSession,
) -> None:
    sid = ingest_session(
        seeded_session,
        SessionEvent(
            tenant_id="t1",
            brand_id="b1",
            visitor_id="v2",
            landing_url="https://acme.com/pricing",
            referrer="https://perplexity.ai/",
            utm={"utm_source": "perplexity"},
            user_agent="Mozilla/5.0",
            ts=datetime.now(UTC),
        ),
    )
    row = seeded_session.query(Session).filter(Session.id == sid).one()
    assert row.referrer == "https://perplexity.ai/"
    assert row.utm == {"utm_source": "perplexity"}
    assert row.user_agent == "Mozilla/5.0"
    assert row.engine is None  # engine classification is a later mechanism (T06), not ingestion


def test_ingest_session_rejects_cross_tenant_event(
    seeded_session: TenantScopedSession,
) -> None:
    # session scoped to t1 must refuse to persist an event carrying a different tenant_id
    with pytest.raises(ValueError):
        ingest_session(
            seeded_session,
            SessionEvent(
                tenant_id="t2",
                brand_id="b1",
                visitor_id="v1",
                landing_url="https://acme.com/x",
                ts=datetime.now(UTC),
            ),
        )


def test_write_key_roundtrip(raw_session: SASession) -> None:
    key = mint_write_key("t1", "b1", salt=SALT)
    assert resolve_write_key(raw_session, key, salt=SALT) == ("t1", "b1")


def test_minted_key_is_opaque_and_write_scoped(raw_session: SASession) -> None:
    key = mint_write_key("t1", "b1", salt=SALT)
    # the key does not expose tenant/brand ids in the clear
    assert "t1" not in key
    assert "b1" not in key


def test_resolve_rejects_garbage_key(raw_session: SASession) -> None:
    with pytest.raises(BadWriteKey):
        resolve_write_key(raw_session, "bad", salt=SALT)


def test_resolve_rejects_wrong_salt(raw_session: SASession) -> None:
    key = mint_write_key("t1", "b1", salt=SALT)
    with pytest.raises(BadWriteKey):
        resolve_write_key(raw_session, key, salt="a-different-salt")


def test_resolve_rejects_tampered_signature(raw_session: SASession) -> None:
    key = mint_write_key("t1", "b1", salt=SALT)
    tampered = key[:-1] + ("0" if key[-1] != "0" else "1")
    with pytest.raises(BadWriteKey):
        resolve_write_key(raw_session, tampered, salt=SALT)


def test_resolve_rejects_unknown_brand(raw_session: SASession) -> None:
    # a validly-signed key for a brand that is not in the DB is rejected (leak tolerance:
    # a key for a deleted/unknown brand cannot write)
    key = mint_write_key("t1", "does-not-exist", salt=SALT)
    with pytest.raises(BadWriteKey):
        resolve_write_key(raw_session, key, salt=SALT)
