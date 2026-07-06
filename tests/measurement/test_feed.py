"""Tests for the dashboards feed query module (M1-T08, m1-design.md §5).

Tenant-scoped read aggregates over `visibility_snapshot` (+ `citation` for the source mix),
exercised against a hermetic in-memory SQLite database (TRD §12). Every test seeds a second
tenant (`t2`) alongside `t1` so leakage across tenants would fail loudly rather than silently.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.db import Base, Brand, Citation, Prompt, Tenant, VisibilitySnapshot
from gw_geo.measurement.feed import (
    citation_source_mix,
    share_of_voice_trend,
    visibility_timeseries,
)


def _seed() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    # FK parents for the seeded snapshots/citations (-> tenant, brand; citation -> prompt too).
    session.add(Tenant(id="t1", name="t", sampling_budget_daily=100.0))
    session.add(Tenant(id="t2", name="t", sampling_budget_daily=100.0))
    session.add(Brand(id="b1", tenant_id="t1", name="b", domain="b.com"))
    session.add(Brand(id="bX", tenant_id="t2", name="b", domain="b.com"))
    session.add(Prompt(id="p1", tenant_id="t1", brand_id="b1", text="q"))
    for date, mention_rate in [("2026-07-01", 0.4), ("2026-07-02", 0.6)]:
        session.add(
            VisibilitySnapshot(
                id=f"s-{date}",
                tenant_id="t1",
                brand_id="b1",
                engine="gemini",
                geo="us",
                persona=None,
                date=date,
                mention_rate=mention_rate,
                citation_rate=0.2,
                avg_position=2.0,
                sentiment_score=0.5,
                share_of_voice=0.3,
                n_samples=10,
                ci_low=0.1,
                ci_high=0.7,
            )
        )
    session.add(
        VisibilitySnapshot(
            id="other",
            tenant_id="t2",
            brand_id="bX",
            engine="gemini",
            geo="us",
            persona=None,
            date="2026-07-02",
            mention_rate=0.9,
            citation_rate=0.9,
            avg_position=1.0,
            sentiment_score=1.0,
            share_of_voice=0.9,
            n_samples=10,
            ci_low=0.8,
            ci_high=1.0,
        )
    )
    session.commit()
    return session


def test_timeseries_is_tenant_scoped_and_ordered() -> None:
    session = _seed()
    rows = visibility_timeseries(
        session, tenant_id="t1", brand_id="b1", since="2026-07-01", until="2026-07-02"
    )
    assert [r["date"] for r in rows] == ["2026-07-01", "2026-07-02"]
    assert rows[1]["mention_rate"] == 0.6
    assert all(r.get("tenant_id", "t1") == "t1" for r in rows)  # never t2's data


def test_timeseries_since_until_window_is_inclusive_and_excludes_outside_dates() -> None:
    session = _seed()
    rows = visibility_timeseries(
        session, tenant_id="t1", brand_id="b1", since="2026-07-02", until="2026-07-02"
    )
    assert [r["date"] for r in rows] == ["2026-07-02"]


def test_timeseries_filters_by_engine_geo_persona() -> None:
    session = _seed()
    rows = visibility_timeseries(
        session,
        tenant_id="t1",
        brand_id="b1",
        engine="openai",
        since="2026-07-01",
        until="2026-07-02",
    )
    assert rows == []


def test_sov_trend_returns_per_date() -> None:
    session = _seed()
    rows = share_of_voice_trend(
        session, tenant_id="t1", brand_id="b1", since="2026-07-01", until="2026-07-02"
    )
    assert len(rows) == 2
    assert all("share_of_voice" in r for r in rows)


def test_sov_trend_is_tenant_scoped() -> None:
    session = _seed()
    rows = share_of_voice_trend(
        session, tenant_id="t1", brand_id="b1", since="2026-07-01", until="2026-07-02"
    )
    # t2's share_of_voice (0.9) must never leak into t1's trend (which seeded 0.3 throughout).
    assert all(r["share_of_voice"] == pytest.approx(0.3) for r in rows)


def test_sov_trend_sample_weights_across_engines_on_same_date() -> None:
    session = _seed()
    # A second engine's snapshot for t1/b1 on 2026-07-02, heavier-weighted (40 vs 10 samples).
    session.add(
        VisibilitySnapshot(
            id="s-2026-07-02-openai",
            tenant_id="t1",
            brand_id="b1",
            engine="openai",
            geo="us",
            persona=None,
            date="2026-07-02",
            mention_rate=0.5,
            citation_rate=0.2,
            avg_position=1.0,
            sentiment_score=0.5,
            share_of_voice=0.8,
            n_samples=40,
            ci_low=0.1,
            ci_high=0.9,
        )
    )
    session.commit()

    rows = share_of_voice_trend(
        session, tenant_id="t1", brand_id="b1", since="2026-07-01", until="2026-07-02"
    )
    by_date = {r["date"]: r for r in rows}
    assert len(rows) == 2
    # (0.3 * 10 + 0.8 * 40) / 50 == 0.7 -- sample-weighted, not a plain average (which is 0.55).
    assert by_date["2026-07-02"]["share_of_voice"] == pytest.approx(0.7)
    assert by_date["2026-07-01"]["share_of_voice"] == pytest.approx(0.3)


def _add_citation(
    session: Session,
    *,
    id: str,
    tenant_id: str,
    brand_id: str,
    source_type: str,
    seen_count: int,
    seen_on: str,
) -> None:
    ts = datetime.strptime(seen_on, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    session.add(
        Citation(
            id=id,
            tenant_id=tenant_id,
            brand_id=brand_id,
            url=f"https://example.com/{id}",
            domain="example.com",
            source_type=source_type,
            engine="gemini",
            prompt_id="p1",
            first_seen=ts,
            last_seen=ts,
            seen_count=seen_count,
        )
    )


def test_citation_source_mix_sums_to_one_and_is_tenant_scoped() -> None:
    session = _seed()
    _add_citation(
        session,
        id="c-reddit",
        tenant_id="t1",
        brand_id="b1",
        source_type="reddit",
        seen_count=3,
        seen_on="2026-07-01",
    )
    _add_citation(
        session,
        id="c-own",
        tenant_id="t1",
        brand_id="b1",
        source_type="own_site",
        seen_count=1,
        seen_on="2026-07-02",
    )
    # Outside the [since, until] window entirely -- must not be counted.
    _add_citation(
        session,
        id="c-old",
        tenant_id="t1",
        brand_id="b1",
        source_type="news_pr",
        seen_count=5,
        seen_on="2026-06-01",
    )
    # A different tenant's citation -- must never leak into t1's mix.
    _add_citation(
        session,
        id="c-other-tenant",
        tenant_id="t2",
        brand_id="bX",
        source_type="wikipedia",
        seen_count=100,
        seen_on="2026-07-01",
    )
    session.commit()

    mix = citation_source_mix(
        session, tenant_id="t1", brand_id="b1", since="2026-07-01", until="2026-07-02"
    )

    assert mix == {"reddit": pytest.approx(0.75), "own_site": pytest.approx(0.25)}
    assert sum(mix.values()) == pytest.approx(1.0)


def test_citation_source_mix_empty_window_returns_empty_dict() -> None:
    session = _seed()
    mix = citation_source_mix(
        session, tenant_id="t1", brand_id="b1", since="2026-01-01", until="2026-01-31"
    )
    assert mix == {}
