"""SQLAlchemy 2.0 schema for TRD §4 (multi-tenant data model) and tenant-scoped session guard.

Column names/types must match `docs/trd.md` §4 exactly. Every table except `Tenant` carries an
indexed `tenant_id` foreign key (TRD §4 preamble + §7: "tenant_id on every row"), enforced here
via `TenantScopedSession` so cross-tenant reads/writes are impossible by construction.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, Query, Session, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all gw_geo ORM tables."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


class TenantScopedSession:
    """Wraps a `Session`, binding it to one `tenant_id` so cross-tenant reads/writes can't happen.

    TRD §7: "a `TenantScopedSession` wrapper injects the filter; no cross-tenant reads."
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self.tenant_id = tenant_id

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
