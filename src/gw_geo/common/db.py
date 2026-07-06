"""SQLAlchemy 2.0 schema for TRD §4 (multi-tenant data model) and tenant-scoped session guard.

Column names/types must match `docs/trd.md` §4 exactly. Every table except `Tenant` carries an
indexed `tenant_id` foreign key (TRD §4 preamble + §7: "tenant_id on every row"), enforced here
via `TenantScopedSession` so cross-tenant reads/writes are impossible by construction.

Each `ForeignKey` column also declares a forward-only many-to-one `relationship()` (no
`back_populates`, no cascade beyond the default save-update/merge). These relationships add no
columns and change no delete behavior; their sole purpose is to give SQLAlchemy the inter-mapper
dependency graph it needs to order flush INSERTs parent-before-child. A `ForeignKey` column alone
does NOT establish that ordering, so on a FK-enforcing backend (Postgres, or SQLite with
`PRAGMA foreign_keys=ON`) a multi-row commit could otherwise insert a child before its parent and
raise a foreign-key violation.
"""

from datetime import datetime, timezone
from typing import Any, TypeVar

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, Query, mapped_column, relationship
from sqlalchemy.orm import Session as SASession


class Base(DeclarativeBase):
    """Declarative base for all gw_geo ORM tables."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# TypeVar for the generic, tenant-scoped `TenantScopedSession.query(model)` accessor. Bound to
# `Base` so any ORM model is accepted; the runtime filter only makes sense for models carrying a
# `tenant_id` column (see `TenantScopedSession.query`).
_ModelT = TypeVar("_ModelT", bound=Base)


class Tenant(Base):
    """A billable/multi-tenant customer boundary. Owns a daily sampling budget (TRD §7)."""

    __tablename__ = "tenant"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    sampling_budget_daily: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class Brand(Base):
    __tablename__ = "brand"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    competitors: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    knowledge_base_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    tenant: Mapped["Tenant"] = relationship()


class Prompt(Base):
    __tablename__ = "prompt"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brand.id"), index=True, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    intent_cluster: Mapped[str | None] = mapped_column(String, nullable=True)
    geo: Mapped[str] = mapped_column(String, default="us", nullable=False)
    persona: Mapped[str | None] = mapped_column(String, nullable=True)
    volume_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    brand: Mapped["Brand"] = relationship()


class ProbeRun(Base):
    __tablename__ = "probe_run"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    prompt_id: Mapped[str] = mapped_column(ForeignKey("prompt.id"), index=True, nullable=False)
    engine: Mapped[str] = mapped_column(String, nullable=False)
    geo: Mapped[str] = mapped_column(String, nullable=False)
    persona: Mapped[str | None] = mapped_column(String, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    raw_answer_s3_key: Mapped[str | None] = mapped_column(String, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    prompt: Mapped["Prompt"] = relationship()


class AnswerExtraction(Base):
    """Per-probe parsed result. TRD §4's column list omits `tenant_id`, but the TRD §4 preamble
    ("tenant_id on every row") and §7 multi-tenancy guarantee require it on every non-Tenant
    table, so it is included here (denormalized off `probe_run.tenant_id`) for a uniform,
    indexable tenant scope. See CONCERNS in the task report.
    """

    __tablename__ = "answer_extraction"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    probe_run_id: Mapped[str] = mapped_column(ForeignKey("probe_run.id"), index=True, nullable=False)
    brand_mentioned: Mapped[bool] = mapped_column(Boolean, nullable=False)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sentiment: Mapped[str] = mapped_column(String, nullable=False)
    cited_urls: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    competitors_present: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    tenant: Mapped["Tenant"] = relationship()
    probe_run: Mapped["ProbeRun"] = relationship()


class Citation(Base):
    __tablename__ = "citation"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brand.id"), index=True, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    engine: Mapped[str] = mapped_column(String, nullable=False)
    prompt_id: Mapped[str] = mapped_column(ForeignKey("prompt.id"), index=True, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    seen_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    brand: Mapped["Brand"] = relationship()
    prompt: Mapped["Prompt"] = relationship()


class VisibilitySnapshot(Base):
    __tablename__ = "visibility_snapshot"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brand.id"), index=True, nullable=False)
    engine: Mapped[str] = mapped_column(String, nullable=False)
    geo: Mapped[str] = mapped_column(String, nullable=False)
    persona: Mapped[str | None] = mapped_column(String, nullable=True)
    date: Mapped[str] = mapped_column(String, nullable=False)
    mention_rate: Mapped[float] = mapped_column(Float, nullable=False)
    citation_rate: Mapped[float] = mapped_column(Float, nullable=False)
    avg_position: Mapped[float | None] = mapped_column(Float, nullable=True)
    sentiment_score: Mapped[float] = mapped_column(Float, nullable=False)
    share_of_voice: Mapped[float] = mapped_column(Float, nullable=False)
    n_samples: Mapped[int] = mapped_column(Integer, nullable=False)
    ci_low: Mapped[float] = mapped_column(Float, nullable=False)
    ci_high: Mapped[float] = mapped_column(Float, nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    brand: Mapped["Brand"] = relationship()


class DriftEvent(Base):
    """Engine drift-canary breach record (m1-design §6, TRD §5.6).

    SYSTEM-LEVEL: intentionally has no `tenant_id` -- engine drift (e.g. Gemini's citation rate
    dropping) is a property of the engine/canary, not of any one tenant, so this is a documented
    exception to the per-row `tenant_id` rule that otherwise applies to every table in this module.
    """

    __tablename__ = "drift_event"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    engine: Mapped[str] = mapped_column(String, index=True)
    canary_id: Mapped[str] = mapped_column(String, index=True)
    baseline_rate: Mapped[float] = mapped_column(Float)
    observed_rate: Mapped[float] = mapped_column(Float)
    drop: Mapped[float] = mapped_column(Float)
    breached: Mapped[bool] = mapped_column(Boolean)
    retrain_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    ts: Mapped[datetime] = mapped_column(DateTime)


class VisibilityRollup(Base):
    """Daily tenant-scoped rollup of `VisibilitySnapshot`, for fast dashboard time-series
    (m1-design §5/§6)."""

    __tablename__ = "visibility_rollup"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenant.id"), index=True)
    brand_id: Mapped[str] = mapped_column(String, index=True)
    engine: Mapped[str] = mapped_column(String, index=True)
    geo: Mapped[str] = mapped_column(String)
    persona: Mapped[str | None] = mapped_column(String, nullable=True)
    date: Mapped[str] = mapped_column(String, index=True)
    mention_rate: Mapped[float] = mapped_column(Float)
    citation_rate: Mapped[float] = mapped_column(Float)
    avg_position: Mapped[float | None] = mapped_column(Float, nullable=True)
    sentiment_score: Mapped[float] = mapped_column(Float)
    share_of_voice: Mapped[float] = mapped_column(Float)
    n_samples: Mapped[int] = mapped_column(Integer)

    tenant: Mapped["Tenant"] = relationship()


class Session(Base):
    """Lead-capture pixel session (m2-design §2.1/§8): one row per beaconed pageview.

    The origin of direct-referral attribution -- `referrer`/`utm` classify the arriving engine
    (`engine`, nullable until classified). Tenant-scoped like every business table.
    """

    __tablename__ = "session"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brand.id"), index=True, nullable=False)
    visitor_id: Mapped[str] = mapped_column(String, nullable=False)
    landing_url: Mapped[str] = mapped_column(String, nullable=False)
    referrer: Mapped[str | None] = mapped_column(String, nullable=True)
    utm: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    engine: Mapped[str | None] = mapped_column(String, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    brand: Mapped["Brand"] = relationship()


class Lead(Base):
    """A captured lead (m2-design §2.1/§8), linked to its originating `session` when known.

    `email`/`value_usd`/`crm_stage`/`self_reported_source` are all optional -- they fill in over
    the lead lifecycle (form capture -> CRM enrichment). Tenant-scoped.
    """

    __tablename__ = "lead"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brand.id"), index=True, nullable=False)
    visitor_id: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("session.id"), index=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    value_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    crm_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    self_reported_source: Mapped[str | None] = mapped_column(String, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    brand: Mapped["Brand"] = relationship()
    session: Mapped["Session | None"] = relationship()


class AttributionLink(Base):
    """One attribution edge (m2-design §2.2-§2.6/§8): ties a `lead`/`session` to an engine via a
    named `method` at a stated `confidence`.

    `method` in {direct, citation_linked, assisted, holdout_incremental};
    `confidence` in {high, medium, reported, modeled, low}. Both are `String` and validated in the
    app layer (attribution package), not the DB. `lead_id`/`session_id` are nullable so a link can
    represent an influenced-but-unconverted session (direct) or a self-reported lead with no tracked
    session (assisted); `citation_id`/`prompt_id` are nullable until the citation-linkage step
    identifies which answer/prompt drove the visit. Tenant-scoped.
    """

    __tablename__ = "attribution_link"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brand.id"), index=True, nullable=False)
    lead_id: Mapped[str | None] = mapped_column(ForeignKey("lead.id"), index=True, nullable=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("session.id"), index=True, nullable=True)
    citation_id: Mapped[str | None] = mapped_column(ForeignKey("citation.id"), index=True, nullable=True)
    prompt_id: Mapped[str | None] = mapped_column(ForeignKey("prompt.id"), index=True, nullable=True)
    engine: Mapped[str] = mapped_column(String, nullable=False)
    method: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[str] = mapped_column(String, nullable=False)
    value_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    brand: Mapped["Brand"] = relationship()
    lead: Mapped["Lead | None"] = relationship()
    session: Mapped["Session | None"] = relationship()
    citation: Mapped["Citation | None"] = relationship()
    prompt: Mapped["Prompt | None"] = relationship()


class HoldoutCohort(Base):
    """A prompt/geo cohort for holdout incrementality (m2-design §2.5/§8).

    `is_holdout` distinguishes the deliberately-un-optimized holdout arm from its optimized
    comparison arm; `prompt_ids` is the JSON set of prompts in the cohort. Tenant-scoped.
    """

    __tablename__ = "holdout_cohort"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brand.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    prompt_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    geo: Mapped[str | None] = mapped_column(String, nullable=True)
    is_holdout: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    brand: Mapped["Brand"] = relationship()


class Integration(Base):
    """A CRM/GA4 connection's persisted state (m2-design §5/§8).

    `config_ref` is a pointer to the encrypted secret (SSM), never the secret itself, and is null
    until connected. `kind` in {hubspot, salesforce, ga4} (app-validated). Tenant-scoped.
    """

    __tablename__ = "integration"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    config_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship()


class AppUser(Base):
    """An authenticating user (m2-design §7).

    SYSTEM-LEVEL: intentionally has NO `tenant_id` -- a user is not owned by a tenant; the
    user<->tenant<->role mapping lives in `Membership`, so one user can belong to several tenants.
    This is a documented exception to the per-row `tenant_id` rule (like `drift_event`).
    """

    __tablename__ = "app_user"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class Membership(Base):
    """The auth join (m2-design §7): grants `user_id` a `role` within `tenant_id`.

    `role` in {owner, admin, editor, viewer} (app-validated). Carries `tenant_id` (so it is
    tenant-scopable) but is the mapping table itself, not a business record.
    """

    __tablename__ = "membership"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), index=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)

    user: Mapped["AppUser"] = relationship()
    tenant: Mapped["Tenant"] = relationship()


class FeatureModel(Base):
    """A trained per-(tenant, brand, engine) ranking-model artifact (m3-design §6/§9-1).

    Interpretable models (GBT default / logistic regression) are trained via an injected
    `ModelBackend` (TRD/m3-design §8); this row is the persisted artifact metadata --
    `feature_names`/`importances` back the `FeatureFactor` explanations surfaced in
    recommendations, `metrics` holds eval scores (e.g. AUC). Tenant-scoped.
    """

    __tablename__ = "feature_model"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brand.id"), index=True, nullable=False)
    engine: Mapped[str] = mapped_column(String, nullable=False)
    model_type: Mapped[str] = mapped_column(String, nullable=False)
    feature_names: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    importances: Mapped[list[float]] = mapped_column(JSON, default=list, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    brand: Mapped["Brand"] = relationship()


class ContentAsset(Base):
    """A generated content draft/asset, on- or off-site (PRD §7 `content_asset`, m3-design §6).

    `prompt_id` (the target prompt the content is shaped for) and `target_engine` are optional --
    an asset may be authored speculatively ahead of a specific prompt/engine pairing, so neither is
    a hard foreign key here (unlike `tenant_id`/`brand_id`). `status` moves through
    draft -> pending_review -> approved -> published (or rejected) per the approval gate
    (m3-design §9-2); `published_url`/`connector`/`published_at` fill in once publishing succeeds.
    Tenant-scoped.
    """

    __tablename__ = "content_asset"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brand.id"), index=True, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    target_engine: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    body_s3_key: Mapped[str | None] = mapped_column(String, nullable=True)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    schema_jsonld: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    published_url: Mapped[str | None] = mapped_column(String, nullable=True)
    connector: Mapped[str | None] = mapped_column(String, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    brand: Mapped["Brand"] = relationship()


class ContentGuardrailReport(Base):
    """Audit trail for the white-hat content gate (PRD NG1, m3-design §9-2): one row per guardrail
    run against a `content_asset`, recording each check's verdict plus the overall `passed` gate
    used as the hard precondition for `approve()`/publish. Tenant-scoped.
    """

    __tablename__ = "content_guardrail_report"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    content_asset_id: Mapped[str] = mapped_column(
        ForeignKey("content_asset.id"), index=True, nullable=False
    )
    originality_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    originality_score: Mapped[float] = mapped_column(Float, nullable=False)
    claims_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    unverified_claims: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    brand_voice_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    brand_voice_score: Mapped[float] = mapped_column(Float, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    content_asset: Mapped["ContentAsset"] = relationship()


class Opportunity(Base):
    """A ranked visibility gap surfaced to the user (m3-design §6/§9-5), e.g. "absent on Gemini".

    `source_gap` names the underlying gap category (e.g. "absence"); `est_impact` is the ranked
    score driving `orchestration/opportunities.py` ordering; `status` moves open -> acted
    (content/action spawned) or -> dismissed. Tenant-scoped.
    """

    __tablename__ = "opportunity"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brand.id"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    rationale: Mapped[str] = mapped_column(String, nullable=False)
    engine: Mapped[str | None] = mapped_column(String, nullable=True)
    est_impact: Mapped[float] = mapped_column(Float, nullable=False)
    source_gap: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    brand: Mapped["Brand"] = relationship()


class BanditArm(Base):
    """One (content_variant x channel) arm of the Thompson-sampling bandit (m3-design §9-4).

    `alpha`/`beta` are the Beta-distribution posterior parameters updated by observed
    `BanditReward`s; `pulls` counts selections. Tenant-scoped.
    """

    __tablename__ = "bandit_arm"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brand.id"), index=True, nullable=False)
    content_variant: Mapped[str] = mapped_column(String, nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    alpha: Mapped[float] = mapped_column(Float, nullable=False)
    beta: Mapped[float] = mapped_column(Float, nullable=False)
    pulls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    brand: Mapped["Brand"] = relationship()


class BanditReward(Base):
    """One observed reward event feeding a `BanditArm`'s posterior (m3-design §9-4).

    `source_snapshot_id` optionally points at the `VisibilitySnapshot` the reward (measurement
    uplift) was derived from; kept as a plain reference (not a hard FK) since rewards may also
    derive from other measurement sources. Tenant-scoped.
    """

    __tablename__ = "bandit_reward"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    arm_id: Mapped[str] = mapped_column(ForeignKey("bandit_arm.id"), index=True, nullable=False)
    reward: Mapped[float] = mapped_column(Float, nullable=False)
    source_snapshot_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    arm: Mapped["BanditArm"] = relationship()


class SeedingChannel(Base):
    """A supported off-site seeding channel (m4-design §2.2/§2.7), e.g. "reddit"/"g2"/"wikipedia" --
    each mapped to a `SourceType`, a ToS ruleset id, and disclosure/UGC placement metadata.

    SYSTEM-LEVEL: intentionally has no `tenant_id` -- the channel catalog is a global, versioned
    reference table shared by every tenant, not owned by one. Documented exception to the per-row
    `tenant_id` rule (same rationale as `DriftEvent`).
    """

    __tablename__ = "seeding_channel"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    tos_ruleset_ref: Mapped[str] = mapped_column(String, nullable=False)
    requires_disclosure: Mapped[bool] = mapped_column(Boolean, nullable=False)
    allows_ugc: Mapped[bool] = mapped_column(Boolean, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ComplianceRule(Base):
    """One white-hat compliance rule evaluated by the compliance engine's hard gate (PRD NG1,
    m4-design §2.4/§2.7). `channel` is `"*"` for global invariants (e.g. `no_astroturf`) or a
    channel name for platform-specific rules (e.g. `reddit_self_promo_ratio`) -- deliberately a
    loose string, not a FK, since `"*"` would not satisfy one. `check_key` indexes the app-layer
    check registry (named `check_key` rather than `check` to avoid the SQL `CHECK` keyword).

    SYSTEM-LEVEL: the ruleset is global reference data, not tenant-owned -- same documented
    exception as `DriftEvent`/`SeedingChannel`.
    """

    __tablename__ = "compliance_rule"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    check_key: Mapped[str] = mapped_column(String, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SeedingTask(Base):
    """One off-site placement task moving through the human-in-the-loop workflow (m4-design
    §2.5/§2.7): `todo -> briefed -> compliance_review -> ready_for_human -> placed ->
    corroborated` (or `-> rejected` on a block-severity compliance failure).

    `content_asset_id` is a loose, optional reference (not a hard FK -- mirrors
    `ContentAsset.prompt_id`) since a task may be briefed without a pre-existing content asset;
    `channel` likewise loosely references `seeding_channel.name` (app-validated), consistent with
    how other cross-subsystem "name" references are kept soft elsewhere in this module.
    `compliance_report` is written by `run_compliance()` and defaults to an empty dict before the
    first compliance pass. Tenant-scoped.
    """

    __tablename__ = "seeding_task"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brand.id"), index=True, nullable=False)
    content_asset_id: Mapped[str | None] = mapped_column(String, nullable=True)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    target_url: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    compliance_status: Mapped[str] = mapped_column(String, nullable=False)
    compliance_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    brief_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    placed_url: Mapped[str | None] = mapped_column(String, nullable=True)
    actor: Mapped[str | None] = mapped_column(String, nullable=True)
    corroboration_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    brand: Mapped["Brand"] = relationship()


class RetrainJob(Base):
    """One triggered retraining run for an engine's ranking model (m4-design §3.1/§2.7): a
    breached `DriftEvent` with `retrain_flag=True` spawns exactly one job via the injected
    `Retrainer` protocol (idempotent per `trigger_drift_event_id`).

    SYSTEM-LEVEL: engine drift/retraining is a property of the engine, not any one tenant -- same
    documented exception as `DriftEvent`, which this table's FK points back to.
    """

    __tablename__ = "retrain_job"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    model_engine: Mapped[str] = mapped_column(String, nullable=False)
    trigger_drift_event_id: Mapped[str] = mapped_column(
        ForeignKey("drift_event.id"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    metrics_before: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metrics_after: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    model_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    trigger_drift_event: Mapped["DriftEvent"] = relationship()


class EffortBanditArm(Base):
    """One (channel, content_variant) arm of the UCB1/Thompson seeding-effort bandit (m4-design
    §3.2/§2.7): `allocate_effort()` ranks arms by measured reward to distribute finite off-site
    placement slots. `arm_key` is `f"{channel}:{variant}"`.

    Named `EffortBanditArm`/`bandit_arm_effort` -- deliberately NOT `BanditArm`/`bandit_arm` -- to
    avoid colliding with M3's `BanditArm` (`bandit_arm`), a *different* subsystem: the Thompson
    content-variant bandit over (content_variant, channel) Beta-posterior (alpha/beta) rewards.
    This is a distinct UCB1-style seeding-effort bandit keyed by a single `arm_key` with raw
    pull/reward-sum/reward-sq-sum statistics. Tenant-scoped; unique per (tenant_id, brand_id,
    arm_key).
    """

    __tablename__ = "bandit_arm_effort"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "brand_id", "arm_key", name="uq_bandit_arm_effort_tenant_brand_arm"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brand.id"), index=True, nullable=False)
    arm_key: Mapped[str] = mapped_column(String, nullable=False)
    pulls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reward_sum: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    reward_sq_sum: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    brand: Mapped["Brand"] = relationship()


class BillingAccount(Base):
    """A tenant's RaaS pricing plan (m4-design §4.2/§4.4): base fee + per-unit `usage_rates`
    (`UsageKind` -> $/unit) plus an optional results-linked (RaaS) charge on attributed
    leads/pipeline (`raas_basis` in {per_lead, pct_pipeline}, app-validated). Tenant-scoped.
    """

    __tablename__ = "billing_account"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    plan: Mapped[str] = mapped_column(String, nullable=False)
    base_fee: Mapped[float] = mapped_column(Float, nullable=False)
    usage_rates: Mapped[dict[str, float]] = mapped_column(JSON, default=dict, nullable=False)
    raas_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raas_basis: Mapped[str] = mapped_column(String, default="per_lead", nullable=False)
    raas_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    currency: Mapped[str] = mapped_column(String, default="USD", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    tenant: Mapped["Tenant"] = relationship()


class UsageEvent(Base):
    """One billable usage record (m4-design §4.1/§4.4) -- a probe run, content generation, or
    seeding placement; `record_usage()`/`meter_period()` write and roll these up per `UsageKind`.
    `brand_id` is nullable for tenant-level usage not tied to one brand. Tenant-scoped.
    """

    __tablename__ = "usage_event"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    brand_id: Mapped[str | None] = mapped_column(ForeignKey("brand.id"), index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)

    tenant: Mapped["Tenant"] = relationship()
    brand: Mapped["Brand | None"] = relationship()


class BillingInvoice(Base):
    """One computed invoice for a billing period (m4-design §4.2/§4.4) -- the persisted form of
    `compute_invoice()`'s `Invoice`: base fee + usage charges + RaaS charge on attributed results.
    `status` tracks the invoice lifecycle (e.g. draft/finalized/paid; app-validated). Tenant-scoped.

    Unique per `(tenant_id, period_start, period_end)` (M5 review): the period-close job's
    check-then-insert idempotency guard (`handlers.close_billing.handler`) is now durably backed by
    a DB constraint, so a concurrent/retried close can never insert a second draft for the same
    period -- the second insert fails the constraint rather than racing the SELECT. Added by
    migration `0007`.
    """

    __tablename__ = "billing_invoice"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "period_start", "period_end", name="uq_billing_invoice_tenant_period"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id"), index=True, nullable=False)
    period_start: Mapped[str] = mapped_column(String, nullable=False)
    period_end: Mapped[str] = mapped_column(String, nullable=False)
    base_fee: Mapped[float] = mapped_column(Float, nullable=False)
    usage_charges: Mapped[dict[str, float]] = mapped_column(JSON, default=dict, nullable=False)
    raas_charge: Mapped[float] = mapped_column(Float, nullable=False)
    attributed_leads: Mapped[int] = mapped_column(Integer, nullable=False)
    attributed_pipeline_usd: Mapped[float] = mapped_column(Float, nullable=False)
    total: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    tenant: Mapped["Tenant"] = relationship()


class LlmModelConfig(Base):
    """The operator-selected content-chat model for one LLM gateway (M5 model-selection).

    One row per `GEO_LLM_GATEWAY` value (`local_claude`/`portkey`/`direct`): `gateway` is the PK and
    `chat_model` the chat-model slug the content-chat factories thread through
    (`content.gateway.resolve_chat_model` -> `build_llm_client`/`build_claim_extractor`/
    `build_voice_scorer`/`build_local_claude_client`). Only the *model* is DB-stored + operator-
    selectable (via `PUT /settings/llm-model`, admin-gated); the *gateway* stays env-driven.

    SYSTEM-LEVEL: intentionally has NO `tenant_id` -- the chat model is a global operator/config
    choice, not tenant-owned (a documented exception to the per-row `tenant_id` rule, like
    `DriftEvent`/`SeedingChannel`/`ComplianceRule`). Seeded with the three gateway defaults by
    migration `0008`; when a row is absent the factories fall back to today's constants
    (`settings.claude_cli_model` for `local_claude`, else `content.gateway.DEFAULT_CHAT_MODEL`).
    """

    __tablename__ = "llm_model_config"

    gateway: Mapped[str] = mapped_column(String, primary_key=True)
    chat_model: Mapped[str] = mapped_column(String, nullable=False)


class TenantScopedSession:
    """Wraps a `Session`, binding it to one `tenant_id` so cross-tenant reads/writes can't happen.

    TRD §7: "a `TenantScopedSession` wrapper injects the filter; no cross-tenant reads."
    """

    def __init__(self, session: SASession, tenant_id: str) -> None:
        self._session = session
        self.tenant_id = tenant_id

    def query(self, model: type[_ModelT]) -> Query[_ModelT]:
        """Query any tenant-scoped `model`, auto-filtered to this session's tenant.

        Works for every model carrying a `tenant_id` column (all M0/M1/M2 business tables plus
        `Membership`). Calling it with a system-level model that has no `tenant_id`
        (e.g. `AppUser`, `DriftEvent`) raises `AttributeError` by design -- those are never
        tenant-scoped.
        """
        return self._session.query(model).filter(getattr(model, "tenant_id") == self.tenant_id)

    def query_brands(self) -> Query[Brand]:
        """Query `Brand` rows, auto-filtered to this session's tenant."""
        return self._session.query(Brand).filter(Brand.tenant_id == self.tenant_id)

    def add(self, obj: Base) -> None:
        """Stage `obj` for insert; rejects objects belonging to a different tenant."""
        obj_tenant_id = getattr(obj, "tenant_id", None)
        if obj_tenant_id != self.tenant_id:
            raise ValueError(
                f"cannot add object with tenant_id={obj_tenant_id!r} to a session scoped to "
                f"tenant_id={self.tenant_id!r}"
            )
        self._session.add(obj)

    def commit(self) -> None:
        self._session.commit()
