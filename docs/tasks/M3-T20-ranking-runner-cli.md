# M3-T20 — Ranking runner + CLI

**Depends on:** T11, T12, T02 · **Wave:** 3 · **Suggested agent:** general-purpose (integration)

**Goal:** Wire the ranking pipeline end-to-end (m3-design §2.6): for each engine, build the dataset
from measurement (T05), train a per-engine model (T11), persist a `feature_model` row (T02), and emit
a `RankingReport` (T12). Expose as `python -m gw_geo.cli rank --brand <id> --engines perplexity,openai`.
All clients (embedder, backend factory, source-mix provider) are **injected**; hermetic.

**Files:**
- Create: `src/gw_geo/ranking/runner.py`
- Edit: `src/gw_geo/cli.py` (add the `rank` subcommand)
- Test: `tests/ranking/test_runner.py`, `tests/test_cli_rank.py`

## Interface

```python
from gw_geo.common.models import RankingReport, FeatureVector, SourceType

def run_ranking(*, session, tenant_id: str, brand_id: str, engines: list[str],
                candidates_by_engine: dict[str, list[dict]],   # {engine: [{"url","features": FeatureVector}]}
                backend_factory, current_by_engine: dict[str, FeatureVector],
                source_mix_by_engine: dict[str, dict[SourceType, float]],
                id_fn=None) -> dict[str, RankingReport]: ...
# per engine: dataset (labels from Citation via T05) -> train -> persist FeatureModel -> build_report
```

## Steps
- [ ] **1. Failing test** `tests/ranking/test_runner.py` (SQLite + seeded `Citation`, fake backend):

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from gw_geo.common.db import Base, Citation, FeatureModel
from gw_geo.common.models import FeatureVector, SourceType
from gw_geo.ranking.runner import run_ranking
from tests.ranking.test_model import FakeBackend

def _session():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); return Session(eng)

def _fv(s):
    return FeatureVector(structure_score=s, info_density=2.0, freshness_days=5.0, domain_authority=0.5,
                         corroboration_count=1, embedding_similarity=0.5, has_schema=True,
                         has_faq=False, table_count=1)

def test_run_ranking_trains_persists_and_reports():
    s = _session()
    s.add(Citation(id="1", tenant_id="t1", brand_id="b1", url="https://a.com/x", domain="a.com",
                   source_type="own_site", engine="perplexity", prompt_id="p1")); s.commit()
    reports = run_ranking(
        session=s, tenant_id="t1", brand_id="b1", engines=["perplexity"],
        candidates_by_engine={"perplexity": [{"url": "https://a.com/x", "features": _fv(0.9)},
                                             {"url": "https://b.com/y", "features": _fv(0.1)}]},
        backend_factory=lambda: FakeBackend(),
        current_by_engine={"perplexity": _fv(0.1)},
        source_mix_by_engine={"perplexity": {SourceType.REDDIT: 0.7}},
        id_fn=lambda: "m1")
    assert "perplexity" in reports and reports["perplexity"].factors
    assert s.get(FeatureModel, "m1") is not None    # model artifact persisted
```

- [ ] **2. Failing test** `tests/test_cli_rank.py` — assert `python -m gw_geo.cli rank ...` parses args
  and calls `run_ranking` (patch/inject the runner; no DB/LLM). Mirror `tests/test_cli.py` style.
- [ ] **3. Run → fail.**
- [ ] **4. Implement** `run_ranking` (uses T05 `cited_urls_for` + `build_dataset`, T11 model, T12
  `build_report`; persists `FeatureModel` with `feature_names`/`importances`/`metrics`) and the `rank`
  CLI subcommand. Real embedder/backend wired via config in the CLI; tests inject fakes.
- [ ] **5. Run → pass**; mypy clean.
- [ ] **6. Commit:** `feat(ranking): ranking runner + `rank` CLI`

## Acceptance
- `run_ranking` builds labels from measurement, trains + persists one `feature_model` per engine, and
  returns per-engine `RankingReport`s; the `rank` CLI subcommand is wired; hermetic.
