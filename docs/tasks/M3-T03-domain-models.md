# M3-T03 — M3 domain models (ranking + content + opportunities)

**Depends on:** M0 models · **Wave:** 0 · **Suggested agent:** general-purpose

**Goal:** Add the Pydantic v2 domain models M3 subsystems share (m3-design §2–§4). These are the
contracts every parallel M3 agent depends on — get names/fields right. They live alongside the M0
models in `common/models.py` and reuse `Brand`, `SourceType`, `VisibilitySnapshot`.

**Files:**
- Edit: `src/gw_geo/common/models.py`
- Test: `tests/common/test_models_m3.py`

## Interface

```python
from enum import StrEnum
from typing import Any
from pydantic import BaseModel, Field

class ContentType(StrEnum):
    ONSITE = "onsite"; OFFSITE = "offsite"

class ContentStatus(StrEnum):
    DRAFT = "draft"; PENDING_REVIEW = "pending_review"
    APPROVED = "approved"; PUBLISHED = "published"; REJECTED = "rejected"

class FeatureVector(BaseModel):
    structure_score: float
    info_density: float                 # stats per 100 words
    freshness_days: float | None
    domain_authority: float
    corroboration_count: int
    embedding_similarity: float
    has_schema: bool
    has_faq: bool
    table_count: int
    def as_list(self, feature_names: list[str]) -> list[float]: ...   # ordered vector for models

class LabeledExample(BaseModel):
    engine: str
    features: FeatureVector
    cited: bool                         # label from measurement

class FeatureFactor(BaseModel):
    name: str
    weight: float
    direction: str                      # "positive" | "negative"
    explanation: str

class ContentGap(BaseModel):
    engine: str
    factor: str
    current_value: float
    target_value: float

class ChannelRecommendation(BaseModel):
    engine: str
    channel: SourceType
    rationale: str
    est_impact: float

class RankingReport(BaseModel):
    engine: str
    factors: list[FeatureFactor] = Field(default_factory=list)
    gaps: list[ContentGap] = Field(default_factory=list)
    channel_recommendations: list[ChannelRecommendation] = Field(default_factory=list)

class Fact(BaseModel):
    id: str
    brand_id: str
    text: str
    category: str = "other"             # usp|product|pricing|certification|claim|other
    source: str | None = None

class ContentDraft(BaseModel):
    id: str
    tenant_id: str
    brand_id: str
    prompt_id: str | None = None
    target_engine: str | None = None
    intent_cluster: str | None = None
    title: str
    body_markdown: str
    schema_jsonld: dict[str, Any] = Field(default_factory=dict)
    grounded_fact_ids: list[str] = Field(default_factory=list)
    status: ContentStatus = ContentStatus.DRAFT

class GuardrailReport(BaseModel):
    originality_ok: bool
    originality_score: float
    claims_ok: bool
    unverified_claims: list[str] = Field(default_factory=list)
    brand_voice_ok: bool
    brand_voice_score: float
    passed: bool

class Opportunity(BaseModel):
    id: str
    tenant_id: str
    brand_id: str
    title: str
    rationale: str
    engine: str | None
    est_impact: float
    source_gap: str                     # absence|source|sentiment
    status: str = "open"

class BanditArm(BaseModel):
    id: str
    tenant_id: str
    brand_id: str
    content_variant: str
    channel: SourceType
    alpha: float = 1.0
    beta: float = 1.0
    pulls: int = 0
```

## Steps
- [ ] **1. Failing test** `tests/common/test_models_m3.py`:

```python
from gw_geo.common.models import (FeatureVector, ContentDraft, ContentStatus,
                                  GuardrailReport, Opportunity, BanditArm, SourceType)

def _fv(**kw):
    base = dict(structure_score=0.5, info_density=3.0, freshness_days=10.0, domain_authority=0.6,
                corroboration_count=4, embedding_similarity=0.8, has_schema=True, has_faq=False,
                table_count=2)
    base.update(kw); return FeatureVector(**base)

def test_feature_vector_as_list_is_ordered():
    fv = _fv()
    names = ["info_density", "domain_authority", "corroboration_count"]
    assert fv.as_list(names) == [3.0, 0.6, 4.0]

def test_draft_defaults_to_draft_status():
    d = ContentDraft(id="c1", tenant_id="t1", brand_id="b1", title="T", body_markdown="x")
    assert d.status == ContentStatus.DRAFT and d.grounded_fact_ids == []

def test_guardrail_and_opportunity_and_arm():
    g = GuardrailReport(originality_ok=True, originality_score=0.1, claims_ok=False,
                        unverified_claims=["revenue tripled"], brand_voice_ok=True,
                        brand_voice_score=0.9, passed=False)
    assert g.passed is False and "revenue tripled" in g.unverified_claims
    o = Opportunity(id="o1", tenant_id="t1", brand_id="b1", title="t", rationale="r",
                    engine="gemini", est_impact=0.7, source_gap="absence")
    assert o.status == "open"
    a = BanditArm(id="a1", tenant_id="t1", brand_id="b1", content_variant="v1",
                  channel=SourceType.REDDIT)
    assert a.alpha == 1.0 and a.channel == SourceType.REDDIT
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** the models. `FeatureVector.as_list(names)` maps names → float attributes
  (ints cast to float). Reuse the existing `SourceType`.
- [ ] **4. Run → pass**; `mypy src/gw_geo/common` clean.
- [ ] **5. Commit:** `feat(common): M3 domain models (ranking/content/opportunity/bandit)`

## Acceptance
- All M3 models validate; `FeatureVector.as_list` yields an ordered numeric vector; defaults match
  the design spec; mypy-strict clean.
