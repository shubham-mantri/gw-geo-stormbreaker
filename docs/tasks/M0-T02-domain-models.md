# M0-T02 — Domain models (Pydantic)

**Depends on:** none · **Wave:** 0 · **Suggested agent:** general-purpose

**Goal:** The shared Pydantic domain models every subsystem uses. These ARE the contracts —
match names/fields to TRD §4 exactly. mypy-strict.

**Files:**
- Create: `src/gw_geo/common/models.py`
- Test: `tests/common/test_models.py`

## Interface (build exactly this)

```python
from enum import StrEnum
from datetime import datetime
from pydantic import BaseModel, Field

class Sentiment(StrEnum):
    POSITIVE = "positive"; NEUTRAL = "neutral"
    NEGATIVE = "negative"; COMPARISON = "comparison"

class SourceType(StrEnum):
    OWN_SITE = "own_site"; REDDIT = "reddit"; WIKIPEDIA = "wikipedia"
    REVIEW_SITE = "review_site"; LISTICLE = "listicle"; NEWS_PR = "news_pr"
    FORUM_QA = "forum_qa"; SOCIAL = "social"; DOCS = "docs"; OTHER = "other"

class Brand(BaseModel):
    id: str; tenant_id: str; name: str; domain: str
    competitors: list[str] = Field(default_factory=list)

class Prompt(BaseModel):
    id: str; tenant_id: str; brand_id: str; text: str
    intent_cluster: str | None = None; geo: str = "us"
    persona: str | None = None; volume_estimate: float | None = None

class ProbeResult(BaseModel):
    engine: str; answer_text: str
    cited_urls: list[str] = Field(default_factory=list)
    raw: dict = Field(default_factory=dict)
    latency_ms: int = 0; cost_usd: float = 0.0

class AnswerExtraction(BaseModel):
    probe_run_id: str; brand_mentioned: bool; position: int | None
    sentiment: Sentiment; cited_urls: list[str]
    source_types: list[SourceType] = Field(default_factory=list)
    competitors_present: list[str] = Field(default_factory=list)

class VisibilitySnapshot(BaseModel):
    brand_id: str; engine: str; geo: str; persona: str | None; date: str
    mention_rate: float; citation_rate: float; avg_position: float | None
    sentiment_score: float; share_of_voice: float
    n_samples: int; ci_low: float; ci_high: float
```

## Steps
- [ ] **1. Failing test** `tests/common/test_models.py`:

```python
from gw_geo.common.models import VisibilitySnapshot, Sentiment, AnswerExtraction

def test_snapshot_roundtrips():
    s = VisibilitySnapshot(brand_id="b1", engine="perplexity", geo="us", persona=None,
        date="2026-07-02", mention_rate=0.4, citation_rate=0.25, avg_position=2.0,
        sentiment_score=0.5, share_of_voice=0.33, n_samples=10, ci_low=0.2, ci_high=0.6)
    assert VisibilitySnapshot.model_validate_json(s.model_dump_json()).n_samples == 10

def test_extraction_requires_sentiment_enum():
    e = AnswerExtraction(probe_run_id="p1", brand_mentioned=True, position=1,
        sentiment=Sentiment.POSITIVE, cited_urls=[])
    assert e.sentiment == "positive"
```

- [ ] **2. Run → fail** (`ModuleNotFoundError`). `pytest tests/common/test_models.py -v`
- [ ] **3. Implement** `src/gw_geo/common/models.py` per the interface above.
- [ ] **4. Run → pass**; `mypy src/gw_geo/common` clean.
- [ ] **5. Commit:** `feat(common): add pydantic domain models`

## Acceptance
- All models above exist with exact field names/types; JSON round-trip works; mypy-strict clean.
