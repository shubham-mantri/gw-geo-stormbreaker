# M1-T15 — Feed rollup (populate `visibility_rollup`)

**Depends on:** T08 (feed queries), T02 (visibility_rollup table) · **Wave:** 2
**Suggested agent:** general-purpose

**Goal:** Populate the `visibility_rollup` table from `visibility_snapshot` — a daily per
`(tenant, brand, engine, geo, persona)` rollup for fast dashboard time-series — and let the T08 feed
queries read the rollup fast path when present (m1-design §5).

**Files:**
- Modify: `src/gw_geo/measurement/feed.py`
- Test: `tests/measurement/test_feed_rollup.py`

## Interface

```python
# added to measurement/feed.py
def build_rollup(session, *, tenant_id: str, date: str) -> int: ...
# reads visibility_snapshot rows for (tenant_id, date), writes/updates one visibility_rollup row
# per (brand_id, engine, geo, persona); returns the number of rollup rows written.

def visibility_timeseries(session, *, tenant_id, brand_id, engine=None, geo=None,
                          persona=None, since, until, use_rollup: bool = True): ...
# when use_rollup=True and rollup rows exist for the window, read visibility_rollup (fast path);
# otherwise fall back to the snapshot query (T08 behavior).
```

`build_rollup` is idempotent: re-running for the same `(tenant_id, date)` upserts (no duplicate
rows). Rollup rows are tenant-scoped.

## Steps
- [ ] **1. Failing test** `tests/measurement/test_feed_rollup.py` (seeded SQLite):

```python
from gw_geo.common.db import Base, VisibilitySnapshot, VisibilityRollup
from gw_geo.measurement.feed import build_rollup, visibility_timeseries
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

def _seed():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); s = Session(eng)
    s.add(VisibilitySnapshot(id="s1", tenant_id="t1", brand_id="b1", engine="gemini",
        geo="us", persona=None, date="2026-07-02", mention_rate=0.5, citation_rate=0.25,
        avg_position=2.0, sentiment_score=0.4, share_of_voice=0.3, n_samples=10,
        ci_low=0.2, ci_high=0.8))
    s.commit(); return s

def test_build_rollup_is_idempotent():
    s = _seed()
    assert build_rollup(s, tenant_id="t1", date="2026-07-02") == 1
    assert build_rollup(s, tenant_id="t1", date="2026-07-02") == 1   # upsert, not duplicate
    rows = s.execute(select(VisibilityRollup)).scalars().all()
    assert len(rows) == 1 and rows[0].mention_rate == 0.5 and rows[0].tenant_id == "t1"

def test_timeseries_reads_rollup_fast_path():
    s = _seed(); build_rollup(s, tenant_id="t1", date="2026-07-02")
    rows = visibility_timeseries(s, tenant_id="t1", brand_id="b1",
                                 since="2026-07-02", until="2026-07-02", use_rollup=True)
    assert rows and rows[0]["mention_rate"] == 0.5
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `build_rollup` (select snapshots for the day, upsert rollup rows keyed by
  `(tenant_id, brand_id, engine, geo, persona, date)`), and extend `visibility_timeseries` with the
  `use_rollup` fast path + snapshot fallback.
- [ ] **4. Run → pass**; mypy clean on touched `common` (if any).
- [ ] **5. Commit:** `feat(measurement): visibility_rollup builder + feed fast path`

## Acceptance
- `build_rollup` produces one tenant-scoped rollup row per `(brand, engine, geo, persona)` per day
  and is idempotent; `visibility_timeseries(use_rollup=True)` reads the rollup and matches the
  snapshot values; hermetic (SQLite).
