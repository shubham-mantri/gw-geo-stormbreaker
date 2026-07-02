"""Request/response models for the API layer (m2-design.md §3).

Response shapes for the read endpoints (overview / visibility / sources / pipeline / ...) are added
by their owning router tasks (T13-T16). This module holds the auth request bodies, the brand
create/list/overview shapes (``routers/brands.py``, T13), the visibility/sources shapes
(``routers/visibility.py``, T14), the prompt/integration/snippet shapes (``routers/settings.py``,
T16), and the pipeline/alerts shapes (``routers/pipeline.py``, T15). The token response reuses
:class:`gw_geo.api.auth.TokenPair` directly (no duplicate model).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from gw_geo.common.models import ContentDraft


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


class PromptCreate(BaseModel):
    """``POST /brands/{id}/prompts`` body (ui-spec.md §6, verbatim): ``text`` is required;
    ``intent_cluster``/``geo``/``persona`` are all optional. A ``None``/omitted ``geo`` is resolved
    to ``"us"`` by the router, matching ``Prompt.geo``'s column default."""

    text: str
    intent_cluster: str | None = None
    geo: str | None = None
    persona: str | None = None


class PromptOut(BaseModel):
    """A prompt as returned by ``GET /brands/{id}/prompts`` (ui-spec.md §6, verbatim)."""

    id: str
    text: str
    intent_cluster: str | None
    geo: str
    persona: str | None


class PromptCreated(BaseModel):
    """``POST /brands/{id}/prompts`` response (ui-spec.md §6, verbatim): the new prompt's id."""

    id: str


class IntegrationConnect(BaseModel):
    """``POST /integrations/{kind}`` body (ui-spec.md §6, verbatim): connector-specific setup, e.g.
    a secret-store pointer under ``"access_token_ref"``/``"credentials_ref"`` -- never a raw
    credential (see ``attribution.integrations``)."""

    config: dict[str, Any]


class IntegrationStatusOut(BaseModel):
    """``POST /integrations/{kind}`` response (ui-spec.md §6, verbatim): the connector's resulting
    status (``"connected"`` or ``"pending"``, per ``attribution.integrations.base.Integration``)."""

    status: str


class SnippetOut(BaseModel):
    """``GET /lead-capture/snippet`` response (ui-spec.md §6, verbatim): the install ``<script>``
    tag, carrying the brand's write-key (``attribution.ingest.mint_write_key``)."""

    snippet: str


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


# --- Content engine (M3-T22, ui-spec §3.5/§6) ------------------------------------------------


class GuardrailBadges(BaseModel):
    """The two guardrail badges the Content screen renders (ui-spec §3.5/§6): the claim-verification
    and originality verdicts. Exactly these two keys, matching ui-spec §6's
    ``guardrails:{claims_ok,originality_ok}`` -- the full :class:`GuardrailReport` (scores,
    brand-voice, the unverified-claim list) stays server-side and is not exposed to the client.
    """

    claims_ok: bool
    originality_ok: bool


class ContentGenerateRequest(BaseModel):
    """``POST /content/generate`` body (ui-spec §3.5/§6): the target search prompt plus the brand to
    scope the draft to. ``tenant_id`` is **never** in the body -- it is derived from the bearer token
    (server-enforced scope, ui-spec §5)."""

    brand_id: str
    prompt_text: str
    target_engine: str | None = None


class ContentGenerateResponse(BaseModel):
    """``POST /content/generate`` response (ui-spec §6, verbatim): the new content id, the editable
    draft, and the two guardrail badges."""

    content_id: str
    draft: ContentDraft
    guardrails: GuardrailBadges


class ContentApproveResponse(BaseModel):
    """``POST /content/{id}/approve`` response (ui-spec §6, verbatim): the draft's resulting
    status (e.g. ``"approved"``)."""

    status: str


class ContentPublishRequest(BaseModel):
    """``POST /content/{id}/publish`` body: which publish connector to target. Defaults to the
    always-available product-hosted subdomain (``hosted``) so a brand with no CMS of its own can
    still publish."""

    connector: str = "hosted"


class ContentPublishResponse(BaseModel):
    """``POST /content/{id}/publish`` response (ui-spec §6, verbatim): the resulting status plus the
    live published URL."""

    status: str
    published_url: str


# --- Opportunities queue (M3-T21, ui-spec §3.4/§6) -------------------------------------------


class OpportunityOut(BaseModel):
    """One row of ``GET /brands/{id}/opportunities`` (ui-spec §3.4/§6, verbatim) -- a ranked
    visibility gap (``orchestration.opportunities.build_opportunities``, T19). Exactly these five
    keys -- the underlying ``Opportunity``'s ``tenant_id``/``brand_id``/``source_gap``/``status``
    stay server-side and are not exposed to the client.
    """

    id: str
    title: str
    rationale: str
    est_impact: float
    engine: str | None


class OpportunityActResponse(BaseModel):
    """``POST /opportunities/{id}/act`` response (ui-spec §3.4/§6, verbatim): the id of the content
    draft the "Fix this ▸" action spawned via the content pipeline (T22)."""

    content_id: str
