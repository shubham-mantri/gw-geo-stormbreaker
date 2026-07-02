# M4-T11 — Corroboration tracking

**Depends on:** T02 (`seeding_task`), T05 (`SourceMap`) · **Wave:** 2
**Suggested agent:** general-purpose

**Goal:** After placements land, measure **how many independent domains** now carry consistent brand
facts — models weight consensus (PRD §6.5, design §2.6). Updates `seeding_task.corroboration_count`
and computes a per-brand corroboration score. Consumes the citation signal via the injected
`SourceMap` protocol (decoupled from M1).

**Files:**
- Create: `src/gw_geo/seeding/corroboration.py`
- Test: `tests/seeding/test_corroboration.py`

## Interface (design §2.6)

```python
from gw_geo.seeding.discovery import SourceMap   # reused protocol

def corroboration_count(source_map: SourceMap, *, tenant_id: str, brand_id: str,
                        since: str, until: str) -> int: ...
#   count of DISTINCT domains citing the brand (you_pct > 0) in the source mix

def update_corroboration(session, source_map: SourceMap, *, tenant_id: str,
                         task_id: str, since: str, until: str) -> int: ...
#   recompute count for the task's brand, write seeding_task.corroboration_count, return it
```

`corroboration_count` counts distinct domains where the brand is now cited (`you_pct > 0`). A domain is
"independent" if it is not the brand's own site (`source_type != own_site`).

## Steps
- [ ] **1. Failing test** `tests/seeding/test_corroboration.py` (SQLite):

```python
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
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); s = Session(eng)
    s.add(SeedingTask(id="st1", tenant_id="t1", brand_id="b1", channel="reddit",
        status="placed", compliance_status="passed", corroboration_count=0)); s.commit()
    n = update_corroboration(s, FakeSourceMap(), tenant_id="t1", task_id="st1",
                             since="a", until="b")
    assert n == 2 and s.get(SeedingTask, "st1").corroboration_count == 2
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** both functions; distinct-domain, independent (non-own-site), `you_pct>0`
  counting; persist to the task row.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(seeding): corroboration tracking`

## Acceptance
- Counts distinct independent domains now citing the brand; excludes own-site and zero-citation rows;
  `update_corroboration` persists to `seeding_task`; hermetic (injected `SourceMap`).
