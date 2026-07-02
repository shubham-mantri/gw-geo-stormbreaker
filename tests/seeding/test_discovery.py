"""Tests for seeding target discovery (m4-design.md S2.1, docs/tasks/M4-T05-target-discovery.md).

`docs/tasks/M4-T05-target-discovery.md` step 1 mandates two tests: `discover_targets` ranks
surviving rows by `gap_score`-derived `priority` and filters out zero-gap / no-active-channel
rows, and `limit` is respected against a larger candidate set. `FakeSourceMap`/`Many` are
hermetic doubles for the injected `SourceMap` protocol -- no live database, no network.
"""

from gw_geo.seeding.channels import ChannelCatalog
from gw_geo.seeding.discovery import discover_targets


class FakeSourceMap:
    def citation_source_mix(self, *, tenant_id, brand_id, since, until):
        return {"sources": [
            {"domain": "reddit.com", "source_type": "reddit", "engine": "perplexity",
             "you_pct": 0.10, "competitor_pct": 0.71},                 # big gap
            {"domain": "g2.com", "source_type": "review_site", "engine": "chatgpt",
             "you_pct": 0.55, "competitor_pct": 0.32},                 # no gap → dropped
            {"domain": "randomblog.io", "source_type": "other", "engine": "gemini",
             "you_pct": 0.0, "competitor_pct": 0.9},                   # no active channel → dropped
        ]}


def test_discovery_ranks_gaps_and_filters():
    targets = discover_targets(FakeSourceMap(), tenant_id="t1", brand_id="b1",
        since="2026-06-01", until="2026-06-30", channels=ChannelCatalog.default())
    assert [t.domain for t in targets] == ["reddit.com"]
    t = targets[0]
    assert round(t.gap_score, 2) == 0.61 and t.channel == "reddit" and t.priority > 0


def test_limit_is_respected():
    class Many:
        def citation_source_mix(self, **k):
            return {"sources": [{"domain": f"reddit{i}.com", "source_type": "reddit",
                     "engine": "perplexity", "you_pct": 0.0, "competitor_pct": 0.9}
                    for i in range(50)]}
    out = discover_targets(Many(), tenant_id="t1", brand_id="b1", since="a", until="b",
                           channels=ChannelCatalog.default(), limit=5)
    assert len(out) == 5
