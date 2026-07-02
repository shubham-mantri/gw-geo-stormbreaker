# M4-T05 — Seeding target discovery (from citation-source map)

**Depends on:** M0 models (consumes an injected `SourceMap`) · **Wave:** 1
**Suggested agent:** general-purpose

**Goal:** Turn the M1 citation-source map into a ranked list of `SeedingTarget`s — the high-authority
channels/domains where competitors are cited but the brand is not (design §2.1). Fully decoupled: the
map is an **injected `SourceMap` protocol** (satisfied by M1 `measurement/feed`), so this builds and
tests before M1 lands.

**Files:**
- Create: `src/gw_geo/seeding/discovery.py`
- Test: `tests/seeding/test_discovery.py`

## Interface (design §2.1)

```python
from typing import Any, Protocol
from pydantic import BaseModel
from gw_geo.common.models import SourceType
from gw_geo.seeding.channels import ChannelCatalog

class SeedingTarget(BaseModel):
    channel: str
    source_type: SourceType
    domain: str
    engine: str
    gap_score: float           # max(competitor_cited_pct - you_cited_pct, 0)
    priority: float            # gap_score * source-authority weight
    rationale: str

class SourceMap(Protocol):     # satisfied by M1 measurement/feed
    def citation_source_mix(self, *, tenant_id: str, brand_id: str,
                            since: str, until: str) -> dict[str, Any]: ...

def discover_targets(source_map: SourceMap, *, tenant_id: str, brand_id: str,
                     since: str, until: str, channels: ChannelCatalog,
                     limit: int = 25) -> list[SeedingTarget]: ...
```

`citation_source_mix` returns rows shaped like
`{"sources": [{"domain","source_type","engine","you_pct","competitor_pct"}, ...]}`. Discovery keeps
only domains whose `source_type` maps to an **active channel**, computes `gap_score` and `priority`,
drops zero-gap rows, sorts by `priority` desc, and truncates to `limit`.

## Steps
- [ ] **1. Failing test** `tests/seeding/test_discovery.py`:

```python
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
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `discover_targets`: map `source_type` → active channel via the catalog, compute
  `gap_score`/`priority` (authority weight may be a simple per-source-type constant), filter/sort/limit.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(seeding): citation-source-map → ranked seeding targets`

## Acceptance
- Returns gaps only, mapped to active channels, sorted by priority, truncated to `limit`; zero-gap and
  no-active-channel rows dropped; fully hermetic (injected `SourceMap`, no live calls).
