"""Shared Pydantic domain models — the contracts every subsystem depends on.

Field names/types must match `docs/trd.md` §4 and §5.2 exactly.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Sentiment(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    COMPARISON = "comparison"


class SourceType(StrEnum):
    OWN_SITE = "own_site"
    REDDIT = "reddit"
    WIKIPEDIA = "wikipedia"
    REVIEW_SITE = "review_site"
    LISTICLE = "listicle"
    NEWS_PR = "news_pr"
    FORUM_QA = "forum_qa"
    SOCIAL = "social"
    DOCS = "docs"
    OTHER = "other"


class Brand(BaseModel):
    id: str
    tenant_id: str
    name: str
    domain: str
    competitors: list[str] = Field(default_factory=list)


class Prompt(BaseModel):
    id: str
    tenant_id: str
    brand_id: str
    text: str
    intent_cluster: str | None = None
    geo: str = "us"
    persona: str | None = None
    volume_estimate: float | None = None


class ProbeResult(BaseModel):
    engine: str
    answer_text: str
    cited_urls: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int = 0
    cost_usd: float = 0.0


class AnswerExtraction(BaseModel):
    probe_run_id: str
    brand_mentioned: bool
    position: int | None
    sentiment: Sentiment
    cited_urls: list[str]
    source_types: list[SourceType] = Field(default_factory=list)
    competitors_present: list[str] = Field(default_factory=list)


class VisibilitySnapshot(BaseModel):
    brand_id: str
    engine: str
    geo: str
    persona: str | None
    date: str
    mention_rate: float
    citation_rate: float
    avg_position: float | None
    sentiment_score: float
    share_of_voice: float
    n_samples: int
    ci_low: float
    ci_high: float


# --- M3: ranking ML + content engine + opportunities (m3-design §2-§4) --------------------


class ContentType(StrEnum):
    ONSITE = "onsite"
    OFFSITE = "offsite"


class ContentStatus(StrEnum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"


class FeatureVector(BaseModel):
    structure_score: float
    info_density: float  # stats per 100 words
    freshness_days: float | None
    domain_authority: float
    corroboration_count: int
    embedding_similarity: float
    has_schema: bool
    has_faq: bool
    table_count: int

    def as_list(self, feature_names: list[str]) -> list[float]:
        """Map ordered feature names to their float values (ints/bools cast to float)."""
        return [float(getattr(self, name)) for name in feature_names]


class LabeledExample(BaseModel):
    engine: str
    features: FeatureVector
    cited: bool  # label from measurement


class FeatureFactor(BaseModel):
    name: str
    weight: float
    direction: str  # "positive" | "negative"
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
    category: str = "other"  # usp|product|pricing|certification|claim|other
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
    source_gap: str  # absence|source|sentiment
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
