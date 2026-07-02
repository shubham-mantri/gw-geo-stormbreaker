"""Tests for corroboration tracking (m4-design.md S2.6, docs/tasks/M4-T11-corroboration.md).

`docs/tasks/M4-T11-corroboration.md` step 1 mandates two tests: `corroboration_count` counts only
the distinct, independent (non-`own_site`) domains where the brand is actually cited (`you_pct >
0`), and `update_corroboration` looks up a `seeding_task` row's brand, recomputes that count, and
persists it back onto the row. `FakeSourceMap` is a hermetic double for the injected `SourceMap`
protocol (T05) -- no live database, no network.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.db import Base, SeedingTask
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


def test_update_writes_count_to_task():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    s.add(SeedingTask(id="st1", tenant_id="t1", brand_id="b1", channel="reddit",
        status="placed", compliance_status="passed", corroboration_count=0))
    s.commit()
    n = update_corroboration(s, FakeSourceMap(), tenant_id="t1", task_id="st1",
                             since="a", until="b")
    assert n == 2 and s.get(SeedingTask, "st1").corroboration_count == 2
