"""Tests for corroboration tracking (m4-design.md S2.6, docs/tasks/M4-T11-corroboration.md).

`docs/tasks/M4-T11-corroboration.md` step 1 mandates two tests: `corroboration_count` counts only
the distinct, independent (non-`own_site`) domains where the brand is actually cited (`you_pct >
0`), and `update_corroboration` looks up a `seeding_task` row's brand, recomputes that count, and
persists it back onto the row. `FakeSourceMap` is a hermetic double for the injected `SourceMap`
protocol (T05) -- no live database, no network.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.db import Base, Brand, SeedingTask, Tenant
from gw_geo.seeding.corroboration import corroboration_count, update_corroboration


class FakeSourceMap:
    def citation_source_mix(self, *, tenant_id, brand_id, since, until):
        return {"sources": [
            {"domain": "reddit.com", "source_type": "reddit", "engine": "perplexity", "you_pct": 0.3},
            {"domain": "g2.com", "source_type": "review_site", "engine": "chatgpt", "you_pct": 0.2},
            {"domain": "acme.com", "source_type": "own_site", "engine": "gemini", "you_pct": 0.9},
            {"domain": "quora.com", "source_type": "forum_qa", "engine": "gemini", "you_pct": 0.0},
        ]}


def test_counts_distinct_independent_domains():
    n = corroboration_count(FakeSourceMap(), tenant_id="t1", brand_id="b1",
                            since="a", until="b")
    assert n == 2       # reddit + g2; own_site excluded, you_pct==0 excluded


def test_update_writes_count_to_task_and_advances_placed_to_corroborated():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    s.add(Tenant(id="t1", name="t", sampling_budget_daily=100.0))
    s.add(Brand(id="b1", tenant_id="t1", name="b", domain="b.com"))
    s.add(SeedingTask(id="st1", tenant_id="t1", brand_id="b1", channel="reddit",
        status="placed", compliance_status="passed", corroboration_count=0))
    s.commit()
    n = update_corroboration(s, FakeSourceMap(), tenant_id="t1", task_id="st1",
                             since="a", until="b")
    task = s.get(SeedingTask, "st1")
    assert n == 2 and task.corroboration_count == 2
    assert task.status == "corroborated"  # placed -> corroborated once corroboration_count > 0


def test_update_does_not_change_status_of_non_placed_task():
    # The placed -> corroborated advance is a no-op for a task not yet PLACED (e.g. still
    # ready_for_human): the count is still written, but the status is left untouched.
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    s.add(Tenant(id="t1", name="t", sampling_budget_daily=100.0))
    s.add(Brand(id="b1", tenant_id="t1", name="b", domain="b.com"))
    s.add(SeedingTask(id="st2", tenant_id="t1", brand_id="b1", channel="reddit",
        status="ready_for_human", compliance_status="passed", corroboration_count=0))
    s.commit()
    n = update_corroboration(s, FakeSourceMap(), tenant_id="t1", task_id="st2",
                             since="a", until="b")
    task = s.get(SeedingTask, "st2")
    assert n == 2 and task.corroboration_count == 2
    assert task.status == "ready_for_human"  # unchanged: only a PLACED task advances
