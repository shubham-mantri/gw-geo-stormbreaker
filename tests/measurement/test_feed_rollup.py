"""Tests for the `visibility_rollup` builder + feed fast path (M1-T15, m1-design.md §5).

Hermetic in-memory SQLite (TRD §12), mirroring `test_feed.py`'s seeding style.
"""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from gw_geo.common.db import Base, VisibilityRollup, VisibilitySnapshot
from gw_geo.measurement.feed import build_rollup, visibility_timeseries


def _seed() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(
        VisibilitySnapshot(
            id="s1",
            tenant_id="t1",
            brand_id="b1",
            engine="gemini",
            geo="us",
            persona=None,
            date="2026-07-02",
            mention_rate=0.5,
            citation_rate=0.25,
            avg_position=2.0,
            sentiment_score=0.4,
            share_of_voice=0.3,
            n_samples=10,
            ci_low=0.2,
            ci_high=0.8,
        )
    )
    session.commit()
    return session


def test_build_rollup_is_idempotent() -> None:
    session = _seed()
    assert build_rollup(session, tenant_id="t1", date="2026-07-02") == 1
    assert build_rollup(session, tenant_id="t1", date="2026-07-02") == 1  # upsert, not duplicate

    rows = session.execute(select(VisibilityRollup)).scalars().all()
    assert len(rows) == 1
    assert rows[0].mention_rate == 0.5
    assert rows[0].tenant_id == "t1"


def test_timeseries_reads_rollup_fast_path() -> None:
    session = _seed()
    build_rollup(session, tenant_id="t1", date="2026-07-02")

    rows = visibility_timeseries(
        session,
        tenant_id="t1",
        brand_id="b1",
        since="2026-07-02",
        until="2026-07-02",
        use_rollup=True,
    )
    assert rows and rows[0]["mention_rate"] == 0.5


def test_build_rollup_is_tenant_scoped() -> None:
    session = _seed()
    session.add(
        VisibilitySnapshot(
            id="s-t2",
            tenant_id="t2",
            brand_id="bX",
            engine="gemini",
            geo="us",
            persona=None,
            date="2026-07-02",
            mention_rate=0.9,
            citation_rate=0.9,
            avg_position=1.0,
            sentiment_score=0.9,
            share_of_voice=0.9,
            n_samples=10,
            ci_low=0.8,
            ci_high=1.0,
        )
    )
    session.commit()

    assert build_rollup(session, tenant_id="t1", date="2026-07-02") == 1

    rows = session.execute(select(VisibilityRollup)).scalars().all()
    assert len(rows) == 1
    assert rows[0].tenant_id == "t1"  # t2's snapshot must never produce a rollup row here


def test_build_rollup_writes_one_row_per_brand_engine_geo_persona() -> None:
    session = _seed()
    session.add(
        VisibilitySnapshot(
            id="s2",
            tenant_id="t1",
            brand_id="b1",
            engine="openai",
            geo="us",
            persona=None,
            date="2026-07-02",
            mention_rate=0.7,
            citation_rate=0.35,
            avg_position=1.5,
            sentiment_score=0.2,
            share_of_voice=0.4,
            n_samples=20,
            ci_low=0.4,
            ci_high=0.9,
        )
    )
    session.commit()

    assert build_rollup(session, tenant_id="t1", date="2026-07-02") == 2

    rows = {row.engine: row for row in session.execute(select(VisibilityRollup)).scalars().all()}
    assert set(rows) == {"gemini", "openai"}
    assert rows["openai"].mention_rate == 0.7
    assert rows["openai"].n_samples == 20


def test_use_rollup_false_bypasses_rollup_and_reads_live_snapshot() -> None:
    session = _seed()
    build_rollup(session, tenant_id="t1", date="2026-07-02")

    # Mutate the rollup row directly so it disagrees with the snapshot -- this proves which
    # table each `use_rollup` setting actually reads from, not just that the values happen to
    # match.
    rollup_row = session.execute(select(VisibilityRollup)).scalars().one()
    rollup_row.mention_rate = 0.99
    session.commit()

    fast_path = visibility_timeseries(
        session,
        tenant_id="t1",
        brand_id="b1",
        since="2026-07-02",
        until="2026-07-02",
        use_rollup=True,
    )
    live = visibility_timeseries(
        session,
        tenant_id="t1",
        brand_id="b1",
        since="2026-07-02",
        until="2026-07-02",
        use_rollup=False,
    )
    assert fast_path[0]["mention_rate"] == 0.99  # served from the (stale) rollup row
    assert live[0]["mention_rate"] == 0.5  # served from the live snapshot row
