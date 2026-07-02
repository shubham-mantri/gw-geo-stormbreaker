"""Tests for assisted modeling (M2-T08, m2-design §2.4) -- attribution mechanism 3, correlational
and always low-confidence (PRD §13 honesty rule). Hermetic: in-memory SQLite, no network.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SASession

from gw_geo.attribution.assisted import (
    _upsert_assisted_link,
    assisted_credit,
    branded_lift_correlation,
)
from gw_geo.common.db import AttributionLink, Base, Brand
from gw_geo.common.db import Lead as LeadRow
from gw_geo.common.db import Session as SessionRow
from gw_geo.common.db import Tenant, TenantScopedSession

_WINDOW_TS = datetime(2026, 6, 15, tzinfo=timezone.utc)
_SINCE, _UNTIL = "2026-06-01", "2026-07-02"


def _seeded_raw() -> SASession:
    """Fresh in-memory SQLite session seeded with tenant `t1` + brand `b1` (same convention as
    `test_linkage.py`/`test_holdout.py`).
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    raw = SASession(engine)
    raw.add(Tenant(id="t1", name="Acme", sampling_budget_daily=100.0))
    raw.add(Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com", competitors=[]))
    raw.commit()
    return raw


def _ts_for(date: str) -> datetime:
    return datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)


@pytest.fixture
def seeded_self_report() -> TenantScopedSession:
    """Lead(self_reported_source="ChatGPT", brand b1, tenant t1) -- the task spec's fixture."""
    raw = _seeded_raw()
    raw.add(
        LeadRow(
            id="lead-1",
            tenant_id="t1",
            brand_id="b1",
            visitor_id="v1",
            self_reported_source="ChatGPT",
            value_usd=500.0,
            ts=_WINDOW_TS,
        )
    )
    raw.commit()
    return TenantScopedSession(raw, "t1")


@pytest.fixture
def seeded_branded() -> TenantScopedSession:
    """One branded/direct lead (no AI referrer, no self-report) for brand b1/tenant t1 -- the
    task spec's fixture. Used with the spec's single-point `visibility_series`, where
    `branded_lift_correlation` is deliberately undefined/zero (fewer than 2 aligned dates), so
    `assisted_credit` produces no `modeled` link for it here;
    `test_branded_lift_creates_modeled_link_with_probabilistic_weight` below exercises the actual
    credit-creation path with a properly aligned multi-day series.
    """
    raw = _seeded_raw()
    raw.add(
        SessionRow(
            id="s-branded",
            tenant_id="t1",
            brand_id="b1",
            visitor_id="v2",
            landing_url="https://acme.com/pricing",
            referrer=None,
            engine=None,
            ts=_WINDOW_TS,
        )
    )
    raw.add(
        LeadRow(
            id="lead-2",
            tenant_id="t1",
            brand_id="b1",
            visitor_id="v2",
            session_id="s-branded",
            value_usd=300.0,
            ts=_WINDOW_TS,
        )
    )
    raw.commit()
    return TenantScopedSession(raw, "t1")


_RISING_VISIBILITY = [
    {"date": "2026-06-01", "share_of_voice": 0.1},
    {"date": "2026-06-02", "share_of_voice": 0.2},
    {"date": "2026-06-03", "share_of_voice": 0.3},
]
_FALLING_VISIBILITY = [
    {"date": "2026-06-01", "share_of_voice": 0.3},
    {"date": "2026-06-02", "share_of_voice": 0.2},
    {"date": "2026-06-03", "share_of_voice": 0.1},
]
_RISING_BRANDED_COUNTS = {"2026-06-01": 1, "2026-06-02": 2, "2026-06-03": 4}


@pytest.fixture
def seeded_branded_correlated() -> TenantScopedSession:
    """1/2/4 branded/direct leads (no AI referrer, no self-report) on 2026-06-01/02/03
    respectively -- rises in lockstep with `_RISING_VISIBILITY`'s `share_of_voice`, so
    `branded_lift_correlation` sees a strong positive `r` and `assisted_credit` actually emits
    `modeled` links (unlike the spec's single-point `seeded_branded` fixture above).
    """
    raw = _seeded_raw()
    i = 0
    for date, n in _RISING_BRANDED_COUNTS.items():
        for _ in range(n):
            i += 1
            sid = f"s-{i}"
            raw.add(
                SessionRow(
                    id=sid,
                    tenant_id="t1",
                    brand_id="b1",
                    visitor_id=f"v-{i}",
                    landing_url="https://acme.com/pricing",
                    referrer=None,
                    engine=None,
                    ts=_ts_for(date),
                )
            )
            raw.add(
                LeadRow(
                    id=f"lead-{i}",
                    tenant_id="t1",
                    brand_id="b1",
                    visitor_id=f"v-{i}",
                    session_id=sid,
                    value_usd=100.0,
                    ts=_ts_for(date),
                )
            )
    raw.commit()
    return TenantScopedSession(raw, "t1")


# -- the task spec's 3 tests (loop variable renamed `l` -> `link`: ruff E741 forbids the
# ambiguous single-letter name `l`, e.g. `test_linkage.py` uses `lk` for the same reason;
# behavior/assertions are otherwise verbatim) --------------------------------------------------


def test_self_report_creates_reported_link(seeded_self_report: TenantScopedSession) -> None:
    links = assisted_credit(
        seeded_self_report,
        tenant_id="t1",
        brand_id="b1",
        since="2026-06-01",
        until="2026-07-02",
        visibility_series=[],
    )
    assert any(link.method == "assisted" and link.confidence == "reported" for link in links)


def test_branded_lift_correlation_positive() -> None:
    vis = [
        {"date": "2026-06-01", "share_of_voice": 0.1},
        {"date": "2026-06-02", "share_of_voice": 0.2},
        {"date": "2026-06-03", "share_of_voice": 0.3},
    ]
    leads = [
        {"date": "2026-06-01", "leads": 10},
        {"date": "2026-06-02", "leads": 20},
        {"date": "2026-06-03", "leads": 30},
    ]
    assert branded_lift_correlation(vis, leads) > 0.9


def test_modeled_link_is_never_high_confidence(seeded_branded: TenantScopedSession) -> None:
    links = assisted_credit(
        seeded_branded,
        tenant_id="t1",
        brand_id="b1",
        since="2026-06-01",
        until="2026-07-02",
        visibility_series=[{"date": "2026-06-01", "share_of_voice": 0.1}],
    )
    assert all(link.confidence in ("reported", "modeled", "low") for link in links)


# -- additional coverage -------------------------------------------------------------------------


def test_self_report_engine_and_confidence_recorded(
    seeded_self_report: TenantScopedSession,
) -> None:
    links = assisted_credit(
        seeded_self_report,
        tenant_id="t1",
        brand_id="b1",
        since=_SINCE,
        until=_UNTIL,
        visibility_series=[],
    )
    assert len(links) == 1
    link = links[0]
    assert link.engine == "chatgpt"
    assert link.method == "assisted" and link.confidence == "reported"
    assert link.lead_id == "lead-1" and link.value_usd == 500.0


def test_self_report_matches_case_insensitively_and_by_host() -> None:
    raw = _seeded_raw()
    raw.add(
        LeadRow(
            id="l1",
            tenant_id="t1",
            brand_id="b1",
            visitor_id="v1",
            self_reported_source="  PERPLEXITY  ",
            value_usd=50.0,
            ts=_WINDOW_TS,
        )
    )
    raw.add(
        LeadRow(
            id="l2",
            tenant_id="t1",
            brand_id="b1",
            visitor_id="v2",
            self_reported_source="claude.ai",
            value_usd=75.0,
            ts=_WINDOW_TS,
        )
    )
    raw.commit()
    scoped = TenantScopedSession(raw, "t1")
    links = assisted_credit(
        scoped, tenant_id="t1", brand_id="b1", since=_SINCE, until=_UNTIL, visibility_series=[]
    )
    by_lead = {link.lead_id: link for link in links}
    assert by_lead["l1"].engine == "perplexity" and by_lead["l1"].confidence == "reported"
    assert by_lead["l2"].engine == "claude" and by_lead["l2"].confidence == "reported"


def test_non_matching_self_report_and_no_session_produces_no_link() -> None:
    raw = _seeded_raw()
    raw.add(
        LeadRow(
            id="l1",
            tenant_id="t1",
            brand_id="b1",
            visitor_id="v1",
            self_reported_source="Google Search",
            value_usd=50.0,
            ts=_WINDOW_TS,
        )
    )
    raw.commit()
    scoped = TenantScopedSession(raw, "t1")
    links = assisted_credit(
        scoped, tenant_id="t1", brand_id="b1", since=_SINCE, until=_UNTIL, visibility_series=[]
    )
    assert links == []


def test_branded_lift_creates_modeled_link_with_probabilistic_weight(
    seeded_branded_correlated: TenantScopedSession,
) -> None:
    lead_series = [{"date": d, "leads": n} for d, n in _RISING_BRANDED_COUNTS.items()]
    expected_r = branded_lift_correlation(_RISING_VISIBILITY, lead_series)
    assert expected_r > 0.9  # strongly positive, but not a perfectly straight line

    links = assisted_credit(
        seeded_branded_correlated,
        tenant_id="t1",
        brand_id="b1",
        since=_SINCE,
        until=_UNTIL,
        visibility_series=_RISING_VISIBILITY,
    )
    modeled = [link for link in links if link.confidence == "modeled"]
    assert len(modeled) == sum(_RISING_BRANDED_COUNTS.values())
    assert all(link.method == "assisted" for link in modeled)
    assert all(link.confidence not in ("high", "medium") for link in modeled)
    assert all(link.engine == "aggregate" for link in modeled)
    for link in modeled:
        assert link.value_usd == pytest.approx(100.0 * expected_r)
        assert link.value_usd < 100.0  # probabilistic weight, never the full/causal value


def test_no_modeled_link_when_correlation_not_positive(
    seeded_branded_correlated: TenantScopedSession,
) -> None:
    links = assisted_credit(
        seeded_branded_correlated,
        tenant_id="t1",
        brand_id="b1",
        since=_SINCE,
        until=_UNTIL,
        visibility_series=_FALLING_VISIBILITY,
    )
    assert links == []


def test_branded_lift_correlation_negative() -> None:
    leads = [{"date": d, "leads": n} for d, n in _RISING_BRANDED_COUNTS.items()]
    assert branded_lift_correlation(_FALLING_VISIBILITY, leads) < -0.9


def test_branded_lift_correlation_handles_degenerate_input() -> None:
    assert branded_lift_correlation([], []) == 0.0

    one_vis = [{"date": "2026-06-01", "share_of_voice": 0.1}]
    one_leads = [{"date": "2026-06-01", "leads": 5}]
    assert branded_lift_correlation(one_vis, one_leads) == 0.0

    flat_dates = ("2026-06-01", "2026-06-02", "2026-06-03")
    flat_vis = [{"date": d, "share_of_voice": 0.2} for d in flat_dates]
    varying_leads = [{"date": d, "leads": n} for d, n in _RISING_BRANDED_COUNTS.items()]
    assert branded_lift_correlation(flat_vis, varying_leads) == 0.0


def test_is_idempotent(seeded_self_report: TenantScopedSession) -> None:
    first = assisted_credit(
        seeded_self_report,
        tenant_id="t1",
        brand_id="b1",
        since=_SINCE,
        until=_UNTIL,
        visibility_series=[],
    )
    second = assisted_credit(
        seeded_self_report,
        tenant_id="t1",
        brand_id="b1",
        since=_SINCE,
        until=_UNTIL,
        visibility_series=[],
    )
    assert len(first) == 1 and len(second) == 1
    assert first[0].id == second[0].id  # same row updated in place, not duplicated
    assert len(seeded_self_report.query(AttributionLink).all()) == 1


def test_rejects_mismatched_tenant_id(seeded_self_report: TenantScopedSession) -> None:
    with pytest.raises(ValueError):
        assisted_credit(
            seeded_self_report,
            tenant_id="t2",
            brand_id="b1",
            since=_SINCE,
            until=_UNTIL,
            visibility_series=[],
        )


def test_upsert_assisted_link_rejects_high_confidence() -> None:
    """Direct unit test of the honesty-rule enforcement (module docstring): even if a future
    change to this module tried to write a `high`/`medium` assisted link, `_upsert_assisted_link`
    refuses.
    """
    raw = _seeded_raw()
    raw.add(LeadRow(id="l1", tenant_id="t1", brand_id="b1", visitor_id="v1", ts=_WINDOW_TS))
    raw.commit()
    scoped = TenantScopedSession(raw, "t1")
    lead = scoped.query(LeadRow).filter(LeadRow.id == "l1").one()
    with pytest.raises(ValueError):
        _upsert_assisted_link(
            scoped,
            tenant_id="t1",
            brand_id="b1",
            lead=lead,
            engine="chatgpt",
            confidence="high",
            value_usd=None,
        )
