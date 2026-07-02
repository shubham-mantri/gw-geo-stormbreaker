"""Request/response models for the API layer (m2-design.md §3).

Response shapes for the read endpoints (overview / visibility / sources / pipeline / ...) are added
by their owning router tasks (T13-T16). This module holds the auth request bodies, the brand
create/list/overview shapes (``routers/brands.py``, T13), the visibility/sources shapes
(``routers/visibility.py``, T14), and the prompt/integration/snippet shapes
(``routers/settings.py``, T16). The token response reuses :class:`gw_geo.api.auth.TokenPair`
directly (no duplicate model).
"""

from __future__ import annotations

from typing import Any

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
