"""Request/response models for the API layer (m2-design.md §3).

Response shapes for the read endpoints (overview / visibility / sources / pipeline / ...) are added
by their owning router tasks (T13-T16). This module holds the auth request bodies, the brand
create/list/overview shapes (``routers/brands.py``, T13), the visibility/sources shapes
(``routers/visibility.py``, T14), and the pipeline/alerts shapes (``routers/pipeline.py``, T15). The
token response reuses :class:`gw_geo.api.auth.TokenPair` directly (no duplicate model).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """``POST /auth/login`` body."""

    email: str
    password: str


class RefreshRequest(BaseModel):
    """``POST /auth/refresh`` body."""

    refresh_token: str


class BrandCreate(BaseModel):
    """``POST /brands`` body (ui-spec.md §6, verbatim).

    ``competitors``/``seed_topics`` are both optional. ``seed_topics`` seeds the onboarding flow's
    prompt-discovery kick-off (``measurement.discover.build_prompt_set``, M0-T11) -- see
    ``routers/brands.py`` for why that kick-off is stubbed (not invoked) here.
    """

    name: str
    domain: str
    competitors: list[str] = Field(default_factory=list)
    seed_topics: list[str] = Field(default_factory=list)


class BrandOut(BaseModel):
    """A brand as returned by ``GET /brands`` (ui-spec.md §6)."""

    id: str
    name: str
    domain: str
    competitors: list[str]


class BrandCreated(BaseModel):
    """``POST /brands`` response (ui-spec.md §6, verbatim): the new brand's id."""

    id: str


class OverviewTrendPoint(BaseModel):
    """One point of the ``you`` vs ``competitor`` share-of-voice series in
    ``GET /brands/{id}/overview`` (ui-spec §3.1/§6, verbatim)."""

    date: str
    you: float
    competitor: float


class OverviewOut(BaseModel):
    """``GET /brands/{id}/overview`` response (ui-spec §3.1/§6, verbatim) -- the landing screen's
    KPI tiles plus the share-of-voice trend."""

    sov: float
    mention_rate: float
    pipeline: float
    leads: int
    trend: list[OverviewTrendPoint]


class VisibilityTrendPoint(BaseModel):
    """One point of an engine's ``trend`` series in ``GET /brands/{id}/visibility`` (ui-spec §3.2)."""

    date: str
    mention_rate: float


class VisibilityEngineOut(BaseModel):
    """One per-engine row of ``GET /brands/{id}/visibility`` (ui-spec §3.2, verbatim).

    ``ci`` is ``[low, high]`` and, with ``n_samples``, is required on every row (TRD §3:
    non-determinism must stay visible). ``cited`` is the citation rate; ``sentiment`` is the raw
    ``[-1, 1]`` sentiment score (this codebase never buckets sentiment into a label -- the ``web/``
    dashboard maps the score to an emoji/label itself).
    """

    engine: str
    mention_rate: float
    ci: tuple[float, float]
    cited: float
    avg_position: float | None
    sentiment: float
    n_samples: int
    trend: list[VisibilityTrendPoint]


class VisibilityPromptOut(BaseModel):
    """One row of ``GET /brands/{id}/visibility``'s ``prompts`` table (ui-spec §3.2, verbatim)."""

    prompt_id: str
    text: str
    mention_rate: float
    avg_position: float | None
    n_samples: int


class VisibilityOut(BaseModel):
    """``GET /brands/{id}/visibility`` response (ui-spec §3.2/§6, verbatim)."""

    engines: list[VisibilityEngineOut]
    prompts: list[VisibilityPromptOut]


class SourceOut(BaseModel):
    """One row of ``GET /brands/{id}/sources`` (ui-spec §3.3/§6, verbatim).

    ``competitor_pcts`` is currently always ``{}`` -- see ``routers/visibility.py`` for why the
    M1 ``citation`` table can't yet attribute a citation to "you" vs a named competitor.
    """

    domain: str
    source_type: str
    you_pct: float
    competitor_pcts: dict[str, float]


class PipelineTopAnswerOut(BaseModel):
    """One row of ``GET /brands/{id}/pipeline``'s ``top_answers`` (ui-spec §3.6, verbatim)."""

    prompt: str
    leads: int
    value: float


class PipelineMethodBreakdownOut(BaseModel):
    """``method_breakdown`` (ui-spec §3.6/§6) -- the anti-overclaim method mix behind
    ``influenced``/``attributed`` (m2-design §1 "non-overclaim rule", PRD §13). Always carries all
    four keys regardless of data: :func:`gw_geo.attribution.pipeline.pipeline_view` (T10) never
    omits a method, only zeroes its figure.
    """

    direct: float
    citation_linked: float
    assisted: float
    holdout_incremental: float


class PipelineOut(BaseModel):
    """``GET /brands/{id}/pipeline`` response (ui-spec §3.6/§6, verbatim) -- validates
    :func:`gw_geo.attribution.pipeline.pipeline_view` (T10)'s output shape unchanged.
    ``confidence_note`` is never empty (the honesty/anti-overclaim rule, PRD §13) and always
    accompanies the headline ``influenced``/``attributed`` numbers.
    """

    influenced: float
    attributed: float
    leads: int
    lift: float
    top_answers: list[PipelineTopAnswerOut]
    method_breakdown: PipelineMethodBreakdownOut
    confidence_note: str


class AlertOut(BaseModel):
    """One row of ``GET /brands/{id}/alerts`` (ui-spec §3.7/§6, verbatim) -- a drift breach (the
    system-level ``drift_event`` table, m1-design §6) or a win detection (e.g. a prompt newly
    ranking the brand #1), severity-tagged for the dashboard's red/green/yellow treatment.
    """

    severity: Literal["red", "green", "yellow"]
    message: str
    ts: datetime
