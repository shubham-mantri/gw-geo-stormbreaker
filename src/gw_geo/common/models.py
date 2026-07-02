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
