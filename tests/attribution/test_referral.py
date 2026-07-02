"""Tests for direct referral capture (M2-T06, m2-design §2.2) -- attribution mechanism 1, the
strongest signal in the four-mechanism stack. Hermetic: in-memory SQLite, no network.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SASession

from gw_geo.attribution.referral import classify_referrer, link_direct
from gw_geo.common.db import Base, Brand, Tenant, TenantScopedSession
from gw_geo.common.db import Session as SessionRow

_WINDOW_TS = datetime(2026, 6, 15, tzinfo=timezone.utc)


def test_classify_by_host() -> None:
    assert classify_referrer("https://chatgpt.com/c/abc", {}) == "chatgpt"
    assert classify_referrer("https://www.perplexity.ai/search", {}) == "perplexity"


def test_classify_by_utm_fallback() -> None:
    assert classify_referrer(None, {"utm_source": "gemini"}) == "gemini"


def test_non_ai_referrer_is_none() -> None:
    assert classify_referrer("https://google.com/search?q=x", {}) is None


@pytest.fixture
def seeded_sessions() -> TenantScopedSession:
    """Tenant `t1`/brand `b1` with 2 sessions in the `2026-06-01..2026-07-02` window: one arriving
    from `chatgpt.com` (should classify + link), one from `google.com` (should not).
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    raw = SASession(engine)
    raw.add(Tenant(id="t1", name="Acme", sampling_budget_daily=100.0))
    raw.add(Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com", competitors=[]))
    raw.add(
        SessionRow(
            id="s-ai",
            tenant_id="t1",
            brand_id="b1",
            visitor_id="v1",
            landing_url="https://acme.com/pricing",
            referrer="https://chatgpt.com/c/abc",
            ts=_WINDOW_TS,
        )
    )
    raw.add(
        SessionRow(
            id="s-organic",
            tenant_id="t1",
            brand_id="b1",
            visitor_id="v2",
            landing_url="https://acme.com/pricing",
            referrer="https://google.com/search?q=acme",
            ts=_WINDOW_TS,
        )
    )
    raw.commit()
    return TenantScopedSession(raw, "t1")


def test_link_direct_creates_links(seeded_sessions: TenantScopedSession) -> None:
    # seeded_sessions: 2 sessions for t1/b1 -- one from chatgpt.com, one from google.com
    links = link_direct(
        seeded_sessions, tenant_id="t1", brand_id="b1", since="2026-06-01", until="2026-07-02"
    )
    assert len(links) == 1
    assert links[0].method == "direct" and links[0].engine == "chatgpt"
    assert links[0].confidence == "high"
