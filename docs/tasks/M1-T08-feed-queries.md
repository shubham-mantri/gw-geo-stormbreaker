# M1-T08 — Dashboards feed query module (tenant-scoped aggregates)

**Depends on:** M0-T04 (db), M0-T02 (models) · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** A read/query layer producing dashboard-ready aggregates from `visibility_snapshot`,
consumed later by the M2 API + `web/` dashboard (m1-design §5). All queries are **tenant-scoped**.
Reads `visibility_snapshot` now; T15 adds the `visibility_rollup` fast path.

**Files:**
- Create: `src/gw_geo/measurement/feed.py`
- Test: `tests/measurement/test_feed.py`

## Interface

```python
from typing import Any

def visibility_timeseries(session, *, tenant_id: str, brand_id: str,
                          engine: str | None = None, geo: str | None = None,
                          persona: str | None = None, since: str, until: str,
                          ) -> list[dict[str, Any]]: ...

def share_of_voice_trend(session, *, tenant_id: str, brand_id: str,
                         since: str, until: str) -> list[dict[str, Any]]: ...

def citation_source_mix(session, *, tenant_id: str, brand_id: str,
                        since: str, until: str) -> dict[str, Any]: ...
```

- `visibility_timeseries` → one row per `date` (optionally filtered by engine/geo/persona) with
  `mention_rate`, `citation_rate`, `avg_position`, `sentiment_score`, `n_samples`.
- `share_of_voice_trend` → one row per `date` with the brand's `share_of_voice` (sample-weighted
  across engines).
- `citation_source_mix` → `{source_type: fraction}` over the `citation` rows in the window.
- **Every** query filters `tenant_id` (no cross-tenant reads); dates are ISO `YYYY-MM-DD` strings
  inclusive of `since`/`until`.

## Steps
- [ ] **1. Failing test** `tests/measurement/test_feed.py` (seeded SQLite):

```python
from gw_geo.common.db import Base, VisibilitySnapshot
from gw_geo.measurement.feed import visibility_timeseries, share_of_voice_trend
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

def _seed():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); s = Session(eng)
    for d, mr in [("2026-07-01", 0.4), ("2026-07-02", 0.6)]:
        s.add(VisibilitySnapshot(id=f"s-{d}", tenant_id="t1", brand_id="b1", engine="gemini",
            geo="us", persona=None, date=d, mention_rate=mr, citation_rate=0.2, avg_position=2.0,
            sentiment_score=0.5, share_of_voice=0.3, n_samples=10, ci_low=0.1, ci_high=0.7))
    s.add(VisibilitySnapshot(id="other", tenant_id="t2", brand_id="bX", engine="gemini",
        geo="us", persona=None, date="2026-07-02", mention_rate=0.9, citation_rate=0.9,
        avg_position=1.0, sentiment_score=1.0, share_of_voice=0.9, n_samples=10, ci_low=0.8, ci_high=1.0))
    s.commit(); return s

def test_timeseries_is_tenant_scoped_and_ordered():
    s = _seed()
    rows = visibility_timeseries(s, tenant_id="t1", brand_id="b1",
                                 since="2026-07-01", until="2026-07-02")
    assert [r["date"] for r in rows] == ["2026-07-01", "2026-07-02"]
    assert rows[1]["mention_rate"] == 0.6
    assert all(r.get("tenant_id", "t1") == "t1" for r in rows)   # never t2's data

def test_sov_trend_returns_per_date():
    s = _seed()
    rows = share_of_voice_trend(s, tenant_id="t1", brand_id="b1",
                                since="2026-07-01", until="2026-07-02")
    assert len(rows) == 2 and all("share_of_voice" in r for r in rows)
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `feed.py` with SQLAlchemy `select(...)` queries filtered by `tenant_id`,
  `brand_id`, and the date window; group/order by `date`. `citation_source_mix` aggregates over the
  `citation` table's `source_type`.
- [ ] **4. Run → pass**; add a `citation_source_mix` test asserting fractions sum to ~1.0.
- [ ] **5. Commit:** `feat(measurement): dashboards feed query module`

## Acceptance
- Three query functions return dashboard-ready aggregates; all are tenant-scoped (a second tenant's
  rows never leak); date windows inclusive; tested against seeded SQLite; hermetic.
