# M3-T05 — Labels + dataset (cited-vs-not from measurement)

**Depends on:** T03 · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** Build the training labels for the ranking models from the measurement system of record
(PRD §6.3, TRD §8): a page/URL is **cited (1)** for `(brand, engine)` iff it appears in that engine's
`Citation` rows; else **0**. Assemble `(features, label)` rows into `list[LabeledExample]`. The DB read
is tenant-scoped (TRD §7); the join is a pure function (testable without a DB).

**Files:**
- Create: `src/gw_geo/ranking/labels.py`, `src/gw_geo/ranking/dataset.py`
- Test: `tests/ranking/test_labels.py`, `tests/ranking/test_dataset.py`

## Interface

```python
# labels.py
def cited_urls_for(session, *, tenant_id: str, brand_id: str, engine: str) -> set[str]: ...
# reads Citation rows (M0) scoped to tenant/brand/engine → set of normalized URLs

# dataset.py
from gw_geo.common.models import LabeledExample
def build_dataset(candidates: list[dict], cited_urls: set[str], *, engine: str,
                  feature_fn) -> list[LabeledExample]: ...
# each candidate: {"url": str, "features": FeatureVector}  (or fields feature_fn consumes)
# label = candidate["url"] in cited_urls
```

## Steps
- [ ] **1. Failing test** `tests/ranking/test_dataset.py`:

```python
from gw_geo.common.models import FeatureVector, LabeledExample
from gw_geo.ranking.dataset import build_dataset

def _fv():
    return FeatureVector(structure_score=0.5, info_density=3.0, freshness_days=5.0,
                         domain_authority=0.6, corroboration_count=2, embedding_similarity=0.7,
                         has_schema=True, has_faq=False, table_count=1)

def test_labels_from_cited_set():
    cands = [{"url": "https://a.com/x", "features": _fv()},
             {"url": "https://b.com/y", "features": _fv()}]
    ds = build_dataset(cands, {"https://a.com/x"}, engine="perplexity",
                       feature_fn=lambda c: c["features"])
    assert len(ds) == 2
    by_url = {c["url"]: e for c, e in zip(cands, ds)}
    assert by_url["https://a.com/x"].cited is True
    assert by_url["https://b.com/y"].cited is False
    assert all(e.engine == "perplexity" for e in ds)
```

- [ ] **2. Failing test** `tests/ranking/test_labels.py` (SQLite + seeded `Citation` rows):

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from gw_geo.common.db import Base, Citation
from gw_geo.ranking.labels import cited_urls_for

def _session():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); return Session(eng)

def test_cited_urls_scoped_to_tenant_brand_engine():
    s = _session()
    s.add(Citation(id="1", tenant_id="t1", brand_id="b1", url="https://a.com/x",
                   domain="a.com", source_type="own_site", engine="perplexity", prompt_id="p1"))
    s.add(Citation(id="2", tenant_id="t1", brand_id="b1", url="https://z.com/q",
                   domain="z.com", source_type="reddit", engine="openai", prompt_id="p1"))
    s.commit()
    urls = cited_urls_for(s, tenant_id="t1", brand_id="b1", engine="perplexity")
    assert urls == {"https://a.com/x"}
```

- [ ] **3. Run → fail.**
- [ ] **4. Implement** `labels.py` (tenant/brand/engine-filtered query over `Citation`) and
  `dataset.py` (pure join). Match `Citation` column names from M0 `db.py`.
- [ ] **5. Run → pass**; mypy clean.
- [ ] **6. Commit:** `feat(ranking): measurement labels + training dataset assembly`

## Acceptance
- `cited_urls_for` returns only tenant/brand/engine-scoped cited URLs; `build_dataset` labels each
  candidate by membership and stamps the engine; hermetic (SQLite only).
