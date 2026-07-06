"""Tests for citation-to-page linkage (M2-T07, m2-design §2.3) -- attribution mechanism 2.

Hermetic: in-memory SQLite, no network. Seeds tenant ``t1`` + brand ``b1`` per the task spec.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SASession

from gw_geo.attribution.linkage import link_citations
from gw_geo.common.db import AttributionLink, Base, Brand, Citation, Prompt
from gw_geo.common.db import Session as SessionRow
from gw_geo.common.db import Tenant, TenantScopedSession

_WINDOW_TS = datetime(2026, 6, 15, tzinfo=UTC)
_SINCE, _UNTIL = "2026-06-01", "2026-07-02"


def _seeded_raw() -> SASession:
    """A fresh in-memory SQLite session seeded with tenant ``t1`` + brand ``b1``."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    raw = SASession(engine)
    raw.add(Tenant(id="t1", name="Acme", sampling_budget_daily=100.0))
    raw.add(Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com", competitors=[]))
    # Citations FK-reference their prompt (citation.prompt_id -> prompt.id); every _seed_citation
    # here uses prompt_id="p1", so seed that prompt once.
    raw.add(Prompt(id="p1", tenant_id="t1", brand_id="b1", text="best CRM"))
    raw.commit()
    return raw


def _seed_citation(
    raw: SASession, *, url: str, engine: str = "perplexity", prompt_id: str = "p1"
) -> None:
    raw.add(
        Citation(
            id="c1",
            tenant_id="t1",
            brand_id="b1",
            url=url,
            domain="acme.com",
            source_type="own_site",
            engine=engine,
            prompt_id=prompt_id,
        )
    )
    raw.commit()


def _seed_session(
    raw: SASession,
    *,
    landing_url: str,
    referrer: str | None = "https://perplexity.ai/",
    engine: str | None = None,
) -> None:
    raw.add(
        SessionRow(
            id="s1",
            tenant_id="t1",
            brand_id="b1",
            visitor_id="v1",
            landing_url=landing_url,
            referrer=referrer,
            engine=engine,
            ts=_WINDOW_TS,
        )
    )
    raw.commit()


@pytest.fixture
def seeded_citation_and_session() -> TenantScopedSession:
    """Citation(url=".../crm-guide", engine="perplexity", prompt_id="p1") +
    Session(landing_url=".../crm-guide?utm=x", referrer="https://perplexity.ai/") -- the same
    page, modulo a non-``utm_``-prefixed tracking param the M0 normalizer alone won't strip.
    """
    raw = _seeded_raw()
    _seed_citation(raw, url="https://acme.com/crm-guide")
    _seed_session(raw, landing_url="https://acme.com/crm-guide?utm=x")
    return TenantScopedSession(raw, "t1")


@pytest.fixture
def seeded_unmatched() -> TenantScopedSession:
    """Citation and session both present, but the session lands on a different page entirely."""
    raw = _seeded_raw()
    _seed_citation(raw, url="https://acme.com/crm-guide")
    _seed_session(raw, landing_url="https://acme.com/pricing")
    return TenantScopedSession(raw, "t1")


def test_links_session_to_cited_page(seeded_citation_and_session: TenantScopedSession) -> None:
    links = link_citations(
        seeded_citation_and_session,
        tenant_id="t1",
        brand_id="b1",
        since=_SINCE,
        until=_UNTIL,
    )
    assert len(links) == 1
    lk = links[0]
    assert lk.method == "citation_linked"
    assert lk.prompt_id == "p1" and lk.engine == "perplexity"


def test_no_match_when_url_differs(seeded_unmatched: TenantScopedSession) -> None:
    assert (
        link_citations(
            seeded_unmatched, tenant_id="t1", brand_id="b1", since=_SINCE, until=_UNTIL
        )
        == []
    )


def test_confidence_and_ids_are_recorded(
    seeded_citation_and_session: TenantScopedSession,
) -> None:
    links = link_citations(
        seeded_citation_and_session, tenant_id="t1", brand_id="b1", since=_SINCE, until=_UNTIL
    )
    lk = links[0]
    assert lk.confidence == "high"
    assert lk.citation_id == "c1"
    assert lk.session_id == "s1"


def test_is_idempotent(seeded_citation_and_session: TenantScopedSession) -> None:
    first = link_citations(
        seeded_citation_and_session, tenant_id="t1", brand_id="b1", since=_SINCE, until=_UNTIL
    )
    second = link_citations(
        seeded_citation_and_session, tenant_id="t1", brand_id="b1", since=_SINCE, until=_UNTIL
    )
    assert len(first) == 1 and len(second) == 1
    assert first[0].id == second[0].id  # same row updated in place, not duplicated
    assert len(seeded_citation_and_session.query(AttributionLink).all()) == 1


def test_rejects_mismatched_tenant_id(seeded_citation_and_session: TenantScopedSession) -> None:
    with pytest.raises(ValueError):
        link_citations(
            seeded_citation_and_session, tenant_id="t2", brand_id="b1", since=_SINCE, until=_UNTIL
        )


def test_no_match_when_session_engine_differs_from_citation_engine() -> None:
    raw = _seeded_raw()
    _seed_citation(raw, url="https://acme.com/crm-guide", engine="perplexity")
    _seed_session(
        raw,
        landing_url="https://acme.com/crm-guide",
        referrer="https://chatgpt.com/",
        engine="chatgpt",
    )
    scoped = TenantScopedSession(raw, "t1")
    assert (
        link_citations(scoped, tenant_id="t1", brand_id="b1", since=_SINCE, until=_UNTIL) == []
    )


def test_matches_when_session_engine_equals_citation_engine() -> None:
    raw = _seeded_raw()
    _seed_citation(raw, url="https://acme.com/crm-guide", engine="perplexity")
    _seed_session(raw, landing_url="https://acme.com/crm-guide", engine="perplexity")
    scoped = TenantScopedSession(raw, "t1")
    links = link_citations(scoped, tenant_id="t1", brand_id="b1", since=_SINCE, until=_UNTIL)
    assert len(links) == 1 and links[0].engine == "perplexity"
