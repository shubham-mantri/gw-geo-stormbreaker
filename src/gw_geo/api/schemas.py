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

# Re-exported so the API layer's response model has a single source of truth: `BrandSuggestion` is
# owned by the onboarding domain module (`POST /brands/suggest` returns it verbatim), the same way
# this module reuses `ContentDraft` from `common.models` rather than duplicating it.
from gw_geo.onboarding.suggest import BrandSuggestion as BrandSuggestion


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


class BrandSuggestRequest(BaseModel):
    """``POST /brands/suggest`` body (M5 domain-first onboarding): the bare domain to look up.

    ``tenant_id`` is **never** in the body -- the endpoint is authed and reads it from the token,
    though it performs no DB write (pure read/suggest). The grounded competitor pipeline runs
    **async** (see :class:`BrandSuggestStarted`/:class:`BrandSuggestStatus`); its eventual
    :class:`BrandSuggestion` fields are pre-filled suggestions the user then edits before
    ``POST /brands``.
    """

    domain: str


class BrandSuggestStarted(BaseModel):
    """``POST /brands/suggest`` **202** response (M5 async onboarding): the started job's id.

    The grounded ~1-2 min competitor pipeline runs on a background thread rather than holding the
    HTTP connection, so the endpoint returns immediately with a ``job_id`` the client then polls via
    ``GET /brands/suggest/status/{job_id}`` (:class:`BrandSuggestStatus`) until ``done``/``error``.
    """

    job_id: str


class BrandSuggestStatus(BaseModel):
    """``GET /brands/suggest/status/{job_id}`` response (M5 async onboarding): the job's live state.

    ``stage``/``label`` are the current pipeline stage (``fetching`` -> ``profiling`` ->
    ``researching`` -> ``refining`` -> ``done``) for a live progress UI. ``result`` is present only
    when ``status == "done"``; ``error`` only when ``status == "error"`` -- on which the client falls
    back to manual entry (onboarding never blocks on a failed lookup).
    """

    status: Literal["running", "done", "error"]
    stage: str
    label: str
    result: BrandSuggestion | None = None
    error: str | None = None


class MeasureTriggerRequest(BaseModel):
    """``POST /brands/{id}/measure`` body (W2 live wiring) -- all optional run overrides.

    Every field defaults to ``None`` so the endpoint can be called with an empty body (or none at
    all): ``engines`` then resolves to every API-keyed engine the runtime has configured, ``geos``
    / ``n_samples`` to the settings defaults, and ``date`` to today (UTC). ``tenant_id`` is
    **never** in the body -- it is derived from the bearer token (server-enforced scope).
    """

    engines: list[str] | None = None
    geos: list[str] | None = None
    n_samples: int | None = None
    date: str | None = None


class MeasureAccepted(BaseModel):
    """``POST /brands/{id}/measure`` **202** response -- describes the run that was scheduled.

    The measurement itself runs asynchronously on a background task (never inline in the request),
    so this is an acknowledgement, not a result: it echoes the resolved ``engines`` + ``n_samples``
    so the caller can see exactly what was enqueued for ``brand_id``.
    """

    status: str
    brand_id: str
    engines: list[str]
    n_samples: int


class AttributionReconcileRequest(BaseModel):
    """``POST /brands/{id}/attribution/reconcile`` body (W4) -- optional window overrides.

    ``since``/``until`` are inclusive ``YYYY-MM-DD`` dates; both default to ``None`` so the endpoint
    can be called with an empty body (or none), in which case the reconcile job sweeps its default
    trailing window (``attribution.trigger._LOOKBACK_DAYS``). ``tenant_id`` is **never** in the body
    -- it is derived from the bearer token (server-enforced scope).
    """

    since: str | None = None
    until: str | None = None


class AttributionReconcileAccepted(BaseModel):
    """``POST /brands/{id}/attribution/reconcile`` **202** response (W4) -- acknowledges that the
    attribution-reconcile batch was scheduled onto a background task.

    Like :class:`MeasureAccepted`/:class:`OpportunityRefreshAccepted`, this is an acknowledgement,
    not a result: the fuzzy attribution writers (direct/citation/assisted) run asynchronously (never
    inline in the request), so the caller gets ``brand_id`` back and then reads the refreshed
    figures from ``GET /brands/{id}/pipeline``.
    """

    status: str
    brand_id: str


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


class LlmModelConfigOut(BaseModel):
    """One row of ``GET /settings/llm-model`` (M5): the operator-selected content-chat model slug
    for one env-driven gateway (``local_claude``/``portkey``/``direct``). System-level config, not
    tenant-scoped."""

    gateway: str
    chat_model: str


class LlmModelConfigUpdate(BaseModel):
    """``PUT /settings/llm-model`` body (M5): upsert the ``chat_model`` for one ``gateway``. The
    *gateway* stays env-driven (``GEO_LLM_GATEWAY``) -- only the *model* is DB-stored/selectable."""

    gateway: str
    chat_model: str


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


class KbFactIn(BaseModel):
    """One fact in the ``POST /brands/{id}/kb/facts`` ingest body -- an approved statement the
    brand's grounding KB may cite (PRD §6.4). ``category`` defaults to ``"other"`` and ``source`` is
    optional, matching :class:`gw_geo.common.models.Fact`'s own defaults; the fact ``id`` and
    ``brand_id`` are assigned server-side (never client-supplied), so a caller can never write into
    another brand's corpus by spoofing them."""

    text: str
    category: str = "other"
    source: str | None = None


class KbFactsIngested(BaseModel):
    """``POST /brands/{id}/kb/facts`` response: how many facts were embedded + upserted into the
    brand's grounding KB."""

    added: int


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


class OpportunityRefreshAccepted(BaseModel):
    """``POST /brands/{id}/opportunities/refresh`` **202** response (W3) -- acknowledges that
    opportunity generation was scheduled onto a background task.

    Like :class:`MeasureAccepted`, this is an acknowledgement, not a result: the ranking + persist
    run happens asynchronously (never inline in the request), so the caller gets ``brand_id`` back
    and then reads the fresh queue from ``GET /brands/{id}/opportunities``.
    """

    status: str
    brand_id: str


class RankingRefreshRequest(BaseModel):
    """``POST /brands/{id}/ranking/refresh`` body (M5) -- optional engine override.

    ``engines`` defaults to ``None`` so the endpoint can be called with an empty body (or none), in
    which case it resolves to every API-keyed engine the runtime has configured. NOTE: ranking
    negatives are sourced cross-engine (a URL another engine cited but this one didn't), so >=2
    engines should be measured for the per-engine models to train (see ``ranking.sourcing``).
    ``tenant_id`` is **never** in the body -- it is derived from the bearer token (server-enforced
    scope).
    """

    engines: list[str] | None = None


class RankingRefreshAccepted(BaseModel):
    """``POST /brands/{id}/ranking/refresh`` **202** response (M5) -- acknowledges that the
    candidate-sourcing ranking run was scheduled onto a background task.

    Like :class:`MeasureAccepted`/:class:`OpportunityRefreshAccepted`, this is an acknowledgement,
    not a result: the crawl (cited URLs) + per-engine train run happens asynchronously (never inline
    in the request), so the caller gets ``brand_id`` + the resolved ``engines`` back and then reads
    the fresh recommendations once the job completes.
    """

    status: str
    brand_id: str
    engines: list[str]
