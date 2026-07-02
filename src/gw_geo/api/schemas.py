"""Request/response models for the API layer (m2-design.md §3).

Response shapes for the read endpoints (overview / visibility / sources / pipeline / ...) are added
by their owning router tasks (T13-T16). This module holds the auth request bodies plus the brand
shapes used by the temporary skeleton ``/brands`` routes (superseded by T13). The token response
reuses :class:`gw_geo.api.auth.TokenPair` directly (no duplicate model).
"""

from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    """``POST /auth/login`` body."""

    email: str
    password: str


class RefreshRequest(BaseModel):
    """``POST /auth/refresh`` body."""

    refresh_token: str


class BrandCreate(BaseModel):
    """``POST /brands`` body (ui-spec.md §6)."""

    name: str
    domain: str


class BrandOut(BaseModel):
    """A brand as returned by ``GET /brands`` (ui-spec.md §6)."""

    id: str
    name: str
    domain: str
    competitors: list[str]


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
