"""Tests for `seeding.sourcemap.CitationSourceMap` -- the AnswerExtraction-backed `SourceMap`
that feeds `discovery.discover_targets` (m4 seeding live-wiring).

Hermetic (TRD S12): in-memory SQLite with FK enforcement ON (see `tests/conftest.py`). Every test
seeds the FK chain `Tenant -> Brand -> Prompt -> ProbeRun -> AnswerExtraction` parent-before-child,
exactly the order a real Postgres FK graph requires.

The load-bearing guarantee here (and the reason this is a DISTINCT class from
`measurement.feed.citation_source_mix`): the you/competitor split is a **mention proxy** derived
from `AnswerExtraction.brand_mentioned` / `.competitors_present`, which the `citation` table cannot
provide -- and the emitted shape is `{"sources": [{domain, source_type, engine, you_pct,
competitor_pct}, ...]}`, NOT `feed`'s flat `{source_type: fraction}`. Swapping the two would hand
`discover_targets` a dict with no `"sources"` key and silently yield zero targets.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session as SASession
from sqlalchemy.pool import StaticPool

from gw_geo.common.db import AnswerExtraction, Base, Brand, Citation, Prompt, ProbeRun, Tenant
from gw_geo.measurement.feed import citation_source_mix as feed_citation_source_mix
from gw_geo.seeding.sourcemap import CitationSourceMap

TENANT = "t1"
BRAND = "b1"
SINCE = "2026-06-01"
UNTIL = "2026-06-30"
_IN_WINDOW = datetime(2026, 6, 15, tzinfo=timezone.utc)
_OUT_OF_WINDOW = datetime(2026, 7, 5, tzinfo=timezone.utc)


@pytest.fixture
def engine() -> Engine:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    return eng


def _seed_parents(session: SASession, *, domain: str = "acme.com") -> None:
    """Seed Tenant -> Brand -> Prompt (each committed before its FK children)."""
    session.add(Tenant(id=TENANT, name="Acme", sampling_budget_daily=100.0))
    session.commit()
    session.add(
        Brand(id=BRAND, tenant_id=TENANT, name="Acme", domain=domain, competitors=["Beta"])
    )
    session.commit()
    session.add(Prompt(id="p1", tenant_id=TENANT, brand_id=BRAND, text="best CRM", geo="us"))
    session.commit()


def _add_extraction(
    session: SASession,
    *,
    pr_id: str,
    ae_id: str,
    engine_name: str,
    ts: datetime,
    brand_mentioned: bool,
    competitors: list[str],
    cited_urls: list[str],
) -> None:
    """Add one ProbeRun + its AnswerExtraction (child after parent), committing each."""
    session.add(
        ProbeRun(
            id=pr_id,
            tenant_id=TENANT,
            prompt_id="p1",
            engine=engine_name,
            geo="us",
            persona=None,
            ts=ts,
            status="ok",
        )
    )
    session.commit()
    session.add(
        AnswerExtraction(
            id=ae_id,
            tenant_id=TENANT,
            probe_run_id=pr_id,
            brand_mentioned=brand_mentioned,
            position=None,
            sentiment="neutral",
            cited_urls=cited_urls,
            competitors_present=competitors,
        )
    )
    session.commit()


def _by_key(mix: dict) -> dict[tuple[str, str], dict]:
    return {(row["engine"], row["domain"]): row for row in mix["sources"]}


def test_shape_and_mention_proxy_math(engine: Engine) -> None:
    # Two perplexity extractions on reddit.com: one mentions the brand (with a competitor), one
    # only a competitor -> you=1/2, competitor=2/2. One chatgpt extraction cites g2.com + own site.
    with SASession(engine) as s:
        _seed_parents(s)
        _add_extraction(
            s, pr_id="pr1", ae_id="ae1", engine_name="perplexity", ts=_IN_WINDOW,
            brand_mentioned=True, competitors=["Beta"], cited_urls=["https://reddit.com/r/x/1"],
        )
        _add_extraction(
            s, pr_id="pr2", ae_id="ae2", engine_name="perplexity", ts=_IN_WINDOW,
            brand_mentioned=False, competitors=["Beta"], cited_urls=["https://reddit.com/r/y/2"],
        )
        _add_extraction(
            s, pr_id="pr3", ae_id="ae3", engine_name="chatgpt", ts=_IN_WINDOW,
            brand_mentioned=True, competitors=[],
            cited_urls=["https://g2.com/acme", "https://acme.com/pricing"],
        )

    with SASession(engine) as s:
        mix = CitationSourceMap(s).citation_source_mix(
            tenant_id=TENANT, brand_id=BRAND, since=SINCE, until=UNTIL
        )

    # Shape: a "sources" list of {domain, source_type, engine, you_pct, competitor_pct} rows.
    assert set(mix) == {"sources"}
    for row in mix["sources"]:
        assert set(row) == {"domain", "source_type", "engine", "you_pct", "competitor_pct"}

    rows = _by_key(mix)
    reddit = rows[("perplexity", "reddit.com")]
    assert reddit["source_type"] == "reddit"
    assert reddit["you_pct"] == 0.5 and reddit["competitor_pct"] == 1.0

    g2 = rows[("chatgpt", "g2.com")]
    assert g2["source_type"] == "review_site"
    assert g2["you_pct"] == 1.0 and g2["competitor_pct"] == 0.0

    # Own domain is classified own_site via brand.domain (has no active channel downstream).
    own = rows[("chatgpt", "acme.com")]
    assert own["source_type"] == "own_site"


def test_engine_and_domain_are_separate_buckets(engine: Engine) -> None:
    # Same domain, two engines -> two distinct (engine, domain) rows.
    with SASession(engine) as s:
        _seed_parents(s)
        _add_extraction(
            s, pr_id="pr1", ae_id="ae1", engine_name="perplexity", ts=_IN_WINDOW,
            brand_mentioned=False, competitors=["Beta"], cited_urls=["https://reddit.com/a"],
        )
        _add_extraction(
            s, pr_id="pr2", ae_id="ae2", engine_name="gemini", ts=_IN_WINDOW,
            brand_mentioned=True, competitors=[], cited_urls=["https://reddit.com/b"],
        )
    with SASession(engine) as s:
        mix = CitationSourceMap(s).citation_source_mix(
            tenant_id=TENANT, brand_id=BRAND, since=SINCE, until=UNTIL
        )
    rows = _by_key(mix)
    assert ("perplexity", "reddit.com") in rows and ("gemini", "reddit.com") in rows
    assert rows[("perplexity", "reddit.com")]["competitor_pct"] == 1.0
    assert rows[("gemini", "reddit.com")]["you_pct"] == 1.0


def test_out_of_window_and_cross_tenant_excluded(engine: Engine) -> None:
    with SASession(engine) as s:
        _seed_parents(s)
        # In window (kept):
        _add_extraction(
            s, pr_id="pr1", ae_id="ae1", engine_name="perplexity", ts=_IN_WINDOW,
            brand_mentioned=False, competitors=["Beta"], cited_urls=["https://reddit.com/keep"],
        )
        # Out of window (dropped by the date filter):
        _add_extraction(
            s, pr_id="pr2", ae_id="ae2", engine_name="perplexity", ts=_OUT_OF_WINDOW,
            brand_mentioned=False, competitors=["Beta"], cited_urls=["https://quora.com/drop"],
        )
    with SASession(engine) as s:
        mix = CitationSourceMap(s).citation_source_mix(
            tenant_id=TENANT, brand_id=BRAND, since=SINCE, until=UNTIL
        )
    domains = {row["domain"] for row in mix["sources"]}
    assert domains == {"reddit.com"}  # quora.com out-of-window extraction excluded

    # Cross-tenant read returns nothing (tenant filter on ProbeRun).
    with SASession(engine) as s:
        other = CitationSourceMap(s).citation_source_mix(
            tenant_id="other", brand_id=BRAND, since=SINCE, until=UNTIL
        )
    assert other == {"sources": []}


def test_shape_is_distinct_from_feed_source_mix(engine: Engine) -> None:
    # The whole point of a DISTINCT class: feed.citation_source_mix returns a flat
    # {source_type: fraction} dict off the `citation` table; CitationSourceMap returns a
    # {"sources": [...]} list off AnswerExtraction. They must never be interchanged.
    with SASession(engine) as s:
        _seed_parents(s)
        _add_extraction(
            s, pr_id="pr1", ae_id="ae1", engine_name="perplexity", ts=_IN_WINDOW,
            brand_mentioned=False, competitors=["Beta"], cited_urls=["https://reddit.com/x"],
        )
        s.add(
            Citation(
                id="c1", tenant_id=TENANT, brand_id=BRAND, url="https://reddit.com/x",
                domain="reddit.com", source_type="reddit", engine="perplexity", prompt_id="p1",
                first_seen=_IN_WINDOW, last_seen=_IN_WINDOW, seen_count=1,
            )
        )
        s.commit()

    with SASession(engine) as s:
        feed_shape = feed_citation_source_mix(
            s, tenant_id=TENANT, brand_id=BRAND, since=SINCE, until=UNTIL
        )
        seeding_shape = CitationSourceMap(s).citation_source_mix(
            tenant_id=TENANT, brand_id=BRAND, since=SINCE, until=UNTIL
        )

    # feed: flat {source_type: float}; seeding: {"sources": [row, ...]}.
    assert "sources" not in feed_shape
    assert feed_shape == {"reddit": 1.0}
    assert "sources" in seeding_shape and isinstance(seeding_shape["sources"], list)
