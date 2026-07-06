"""On-site content pipeline (`docs/m3-design.md` §3.6, PRD §6.4) -- the honesty gate, composed.

`ContentService` orchestrates one draft through the full white-hat flow that the `/content` API
endpoints call:

    ground -> generate (T14) -> run_guardrails (T16) -> persist -> (approval gate, T17) -> publish (T18)

The gate is enforced by *composition*, not by convention:

* `approve()` delegates to `content.approval.approve` (T17), which refuses unless the
  `GuardrailReport.passed` AND the reviewer's `role` is authorized -- neither alone suffices.
* `publish()` calls `content.approval.ensure_publishable(draft)` **first**, before a connector is
  even resolved, so a draft that is not `APPROVED` can never reach a `PublishConnector`. This is
  what makes Athena's documented failure (an unreviewed/plagiarized/fabricated draft reaching
  publish) structurally impossible here.

Every collaborator is **injected** (LLM, corpus search, claim extractor, voice scorer, KB, publish
connectors, and the asset store), so the whole pipeline is hermetically testable with in-memory
fakes -- no live LLM / embedding / search / HTTP / DB call (`docs/trd.md` §12).

Persistence is a seam -- the `AssetStore`. `generate` writes the authoritative `(draft, report)`
per `(tenant_id, content_id)` to the store; `get_asset`/`approve`/`publish` read/update it, so the
gate always runs over the *server-side* draft, never a client-supplied one. Two implementations:

* `InMemoryAssetStore` (the DEFAULT when no `store` is injected) -- a process-local dict, exactly
  the behavior this service always had; hermetic unit tests inject nothing and get it.
* `DbAssetStore` -- persists to the `content_asset` + `content_guardrail_report` tables
  (`gw_geo.common.db`), so a generate in one HTTP request is resolvable by an approve/publish in a
  later, separate request (which the in-memory store, scoped to one process instance, cannot span).

KB grounding is also a seam. A single fixed `kb` is the default (hermetic tests inject one). When
an optional `kb_factory` is injected instead, `generate` grounds + claim-verifies against
`kb_factory(brand.id)` -- a per-brand KB built from the brand's own vector namespace -- so grounding
can never cross a brand boundary. `ground()` exposes that same per-brand retrieval to the API layer.

Tenant scoping (m2-design's tenancy model, TRD §7): every asset is keyed by `(tenant_id,
content_id)`, never `content_id` alone, and `get_asset`/`approve`/`publish` all take an explicit
`tenant_id` that must match the asset's own -- a mismatch raises `LookupError` (-> **404** via
M2's app-level handler), exactly like an unowned brand (`routers/brands.py`'s
`_ensure_brand_owned`). "Doesn't exist" and "exists but belongs to another tenant" deliberately
collapse to the same 404, so a foreign tenant's content id is never confirmed to exist (no IDOR:
a tenant-B caller can never approve/publish/read tenant-A's draft by guessing its id).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy.orm import Session as SASession

from gw_geo.billing.metering import UsageKind, record_usage
from gw_geo.common.db import ContentAsset, ContentGuardrailReport
from gw_geo.common.models import (
    Brand,
    ContentDraft,
    ContentStatus,
    ContentType,
    Fact,
    GuardrailReport,
    RankingReport,
)
from gw_geo.content.approval import approve as _approve
from gw_geo.content.approval import ensure_publishable, submit_for_review
from gw_geo.content.generate import LLMClient, generate_draft
from gw_geo.content.guardrails.brand_voice import VoiceScorer
from gw_geo.content.guardrails.claims import ClaimExtractor
from gw_geo.content.guardrails.originality import CorpusSearch
from gw_geo.content.guardrails.runner import GuardrailThresholds, run_guardrails
from gw_geo.content.kb import KnowledgeBase
from gw_geo.content.publish.base import PublishConnector, PublishResult, get_connector
from gw_geo.content.publish.metadata import freshness_meta


class AssetStore(Protocol):
    """The persistence seam for `(draft, GuardrailReport)` pairs, keyed by `(tenant_id, id)`.

    Injected into `ContentService` so the pipeline is agnostic to *where* drafts live: an in-memory
    dict for the hermetic tests (`InMemoryAssetStore`), or the `content_asset` +
    `content_guardrail_report` tables for the real HTTP service (`DbAssetStore`).
    """

    def save(self, draft: ContentDraft, report: GuardrailReport) -> None:
        """Insert or replace the `(draft, report)` keyed by `(draft.tenant_id, draft.id)`."""
        ...

    def get(
        self, *, tenant_id: str, content_id: str
    ) -> tuple[ContentDraft, GuardrailReport]:
        """Return the stored `(draft, report)` for `content_id` within `tenant_id`.

        Raises `LookupError` when no asset exists under `content_id` for `tenant_id` -- either it
        does not exist at all, or it exists under a *different* tenant (the two are deliberately
        indistinguishable, so the store never confirms a foreign tenant's id exists).
        """
        ...

    def mark_published(
        self, draft: ContentDraft, *, published_url: str, connector: str
    ) -> None:
        """Record the `PUBLISHED` transition (and, where the backend supports it, the publish
        metadata) for an already-stored asset. A no-op if the asset is not present."""
        ...


class InMemoryAssetStore:
    """Process-local `AssetStore` -- the default when `ContentService` is built without a `store`.

    Byte-identical to the service's original in-memory dict: the `(tenant_id, content_id)` tuple key
    is the IDOR fix (a content id alone is not enough to resolve an asset), and `mark_published`
    only writes back an *already-present* asset (never conjures a new one), so an unstored draft
    that somehow reaches publish leaves no phantom row.
    """

    def __init__(self) -> None:
        self._assets: dict[tuple[str, str], tuple[ContentDraft, GuardrailReport]] = {}

    def save(self, draft: ContentDraft, report: GuardrailReport) -> None:
        self._assets[(draft.tenant_id, draft.id)] = (draft, report)

    def get(
        self, *, tenant_id: str, content_id: str
    ) -> tuple[ContentDraft, GuardrailReport]:
        try:
            return self._assets[(tenant_id, content_id)]
        except KeyError as exc:
            raise LookupError(f"content asset {content_id!r} not found") from exc

    def mark_published(
        self, draft: ContentDraft, *, published_url: str, connector: str
    ) -> None:
        key = (draft.tenant_id, draft.id)
        if key in self._assets:
            self._assets[key] = (draft, self._assets[key][1])


class DbAssetStore:
    """`AssetStore` backed by the `content_asset` + `content_guardrail_report` tables (TRD §4).

    Bound to one `tenant_id` (like `TenantScopedSession`): every write stamps it and every read
    filters on it, so the shared tables can never leak one tenant's drafts into another's. The
    `GuardrailReport` maps 1:1 to `content_guardrail_report`. The `ContentDraft` maps to
    `content_asset`'s columns, with the three fields that have no dedicated column
    (`body_markdown`, `intent_cluster`, `grounded_fact_ids`) carried in the free-form `features`
    JSON: this self-contained build stores the markdown body inline there rather than offloading it
    to S3, so `body_s3_key` stays `NULL` (S3 offload is a production follow-on -- see CONCERNS).

    `save`/`mark_published` `commit()` so a write in one request's session is visible to the fresh
    session a later request opens -- the whole point of a DB-backed store over the in-memory one.
    """

    def __init__(self, *, session: SASession, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def save(self, draft: ContentDraft, report: GuardrailReport) -> None:
        if draft.tenant_id != self._tenant_id:
            raise ValueError(
                f"cannot save draft with tenant_id={draft.tenant_id!r} to a store scoped to "
                f"tenant_id={self._tenant_id!r}"
            )
        asset = self._session.get(ContentAsset, draft.id)
        if asset is None:
            asset = ContentAsset(id=draft.id, tenant_id=draft.tenant_id, created_at=_utcnow())
            self._session.add(asset)
        asset.tenant_id = draft.tenant_id
        asset.brand_id = draft.brand_id
        asset.type = ContentType.ONSITE.value
        asset.target_engine = draft.target_engine
        asset.prompt_id = draft.prompt_id
        asset.title = draft.title
        asset.schema_jsonld = draft.schema_jsonld
        asset.features = {
            "body_markdown": draft.body_markdown,
            "intent_cluster": draft.intent_cluster,
            "grounded_fact_ids": list(draft.grounded_fact_ids),
        }
        asset.status = draft.status.value
        self._upsert_report(draft.id, draft.tenant_id, report)
        self._session.commit()

    def get(
        self, *, tenant_id: str, content_id: str
    ) -> tuple[ContentDraft, GuardrailReport]:
        asset = self._session.get(ContentAsset, content_id)
        if asset is None or asset.tenant_id != tenant_id:
            raise LookupError(f"content asset {content_id!r} not found")
        report_row = (
            self._session.query(ContentGuardrailReport)
            .filter_by(content_asset_id=content_id, tenant_id=tenant_id)
            .one_or_none()
        )
        if report_row is None:
            raise LookupError(f"content asset {content_id!r} not found")
        features = asset.features or {}
        draft = ContentDraft(
            id=asset.id,
            tenant_id=asset.tenant_id,
            brand_id=asset.brand_id,
            prompt_id=asset.prompt_id,
            target_engine=asset.target_engine,
            intent_cluster=features.get("intent_cluster"),
            title=asset.title,
            body_markdown=features.get("body_markdown", ""),
            schema_jsonld=asset.schema_jsonld,
            grounded_fact_ids=features.get("grounded_fact_ids", []),
            status=ContentStatus(asset.status),
        )
        report = GuardrailReport(
            originality_ok=report_row.originality_ok,
            originality_score=report_row.originality_score,
            claims_ok=report_row.claims_ok,
            unverified_claims=report_row.unverified_claims,
            brand_voice_ok=report_row.brand_voice_ok,
            brand_voice_score=report_row.brand_voice_score,
            passed=report_row.passed,
            # `originality_enforced` has no column (no schema change for an audit-only flag), so a
            # reloaded report takes the model default (True). The truthful, durable signal for the
            # LOCAL no-corpus case is the generation-time `logger.warning` in `api/wiring.py`.
        )
        return draft, report

    def mark_published(
        self, draft: ContentDraft, *, published_url: str, connector: str
    ) -> None:
        asset = self._session.get(ContentAsset, draft.id)
        if asset is None or asset.tenant_id != draft.tenant_id:
            return
        asset.status = ContentStatus.PUBLISHED.value
        asset.published_url = published_url
        asset.connector = connector
        asset.published_at = _utcnow()
        self._session.commit()

    def _upsert_report(
        self, content_asset_id: str, tenant_id: str, report: GuardrailReport
    ) -> None:
        row = (
            self._session.query(ContentGuardrailReport)
            .filter_by(content_asset_id=content_asset_id, tenant_id=tenant_id)
            .one_or_none()
        )
        if row is None:
            row = ContentGuardrailReport(
                id=uuid4().hex,
                tenant_id=tenant_id,
                content_asset_id=content_asset_id,
                ts=_utcnow(),
            )
            self._session.add(row)
        row.originality_ok = report.originality_ok
        row.originality_score = report.originality_score
        row.claims_ok = report.claims_ok
        row.unverified_claims = list(report.unverified_claims)
        row.brand_voice_ok = report.brand_voice_ok
        row.brand_voice_score = report.brand_voice_score
        row.passed = report.passed


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ContentService:
    """Compose generation + guardrails + the approval gate + publishing for one brand.

    All collaborators are injected. `connectors` is a name->`PublishConnector` map (injected fakes
    in tests); an unknown name falls back to the process-global `publish.base` registry populated by
    `publish.wiring.register_default_connectors`, so real wiring keeps working via `get_connector`.
    `thresholds=None` lets `run_guardrails` build the fail-closed thresholds from `Settings` (T01).

    Exactly one of `kb` (a fixed per-service KB, the hermetic-test default) or `kb_factory` (a
    per-brand KB builder, the real-wiring path) must be supplied. `store=None` selects the
    `InMemoryAssetStore` default, so a service built without a session behaves exactly as before.
    """

    def __init__(
        self,
        *,
        llm: LLMClient,
        corpus: CorpusSearch,
        claim_extractor: ClaimExtractor,
        voice_scorer: VoiceScorer,
        voice_profile: dict[str, Any],
        connectors: Mapping[str, PublishConnector],
        kb: KnowledgeBase | None = None,
        kb_factory: Callable[[str], KnowledgeBase] | None = None,
        store: AssetStore | None = None,
        thresholds: GuardrailThresholds | None = None,
        id_fn: Callable[[], str] | None = None,
        usage_session: SASession | None = None,
        originality_enforced: bool = True,
    ) -> None:
        if kb is None and kb_factory is None:
            raise ValueError("ContentService requires either `kb` or `kb_factory`")
        self._kb = kb
        self._kb_factory = kb_factory
        self._llm = llm
        self._corpus = corpus
        self._claim_extractor = claim_extractor
        self._voice_scorer = voice_scorer
        self._voice_profile = voice_profile
        self._connectors = connectors
        self._thresholds = thresholds
        self._id_fn = id_fn
        # M5 review (honesty): recorded onto each generated `GuardrailReport` as an audit signal.
        # The real-wiring path (`api/wiring.py`) passes `False` because it injects a no-op corpus
        # (no `CorpusSearch` backend configured), so originality is not actually enforced. This does
        # NOT gate `passed` -- see `run_guardrails` / `GuardrailReport.originality_enforced`.
        self._originality_enforced = originality_enforced
        # Optional billing-metering seam: when a session is injected (the real DB-backed wiring), a
        # GENERATION usage unit is recorded per generated asset. `None` (the hermetic in-memory
        # default) meters nothing, so existing in-memory tests are unaffected.
        self._usage_session = usage_session
        # Authoritative server-side (draft, report) per (tenant_id, content_id) -- the id-addressed
        # approve/publish endpoints resolve against this, never a client-supplied draft, and never
        # across a tenant boundary (the tuple key is the IDOR fix). The store abstracts whether that
        # lives in-process (default) or in the DB (real HTTP service).
        self._store: AssetStore = store if store is not None else InMemoryAssetStore()

    def _resolve_kb(self, brand_id: str) -> KnowledgeBase:
        """The KB to ground + claim-verify against for `brand_id`.

        `kb_factory(brand_id)` when a factory is injected (per-brand, real wiring), else the fixed
        `self._kb` (hermetic tests). The `__init__` guard guarantees at least one is present.
        """
        if self._kb_factory is not None:
            return self._kb_factory(brand_id)
        assert self._kb is not None  # guaranteed by __init__
        return self._kb

    def ground(self, *, brand_id: str, prompt_text: str, top_k: int = 5) -> list[Fact]:
        """Retrieve the grounding `Fact`s for `prompt_text` from `brand_id`'s KB.

        The `/content/generate` endpoint calls this to hydrate the facts a draft may state, using
        the *same* per-brand KB `generate` then claim-verifies against -- so grounding and
        verification always agree on the brand's source of truth.
        """
        return self._resolve_kb(brand_id).ground(prompt_text, top_k=top_k)

    def generate(
        self,
        *,
        brand: Brand,
        prompt_text: str,
        facts: list[Fact],
        feature_profile: RankingReport | None,
        target_engine: str | None = None,
    ) -> tuple[ContentDraft, GuardrailReport]:
        """Generate a grounded draft (T14) and run all three guardrails against it (T16).

        `facts` are the grounding facts the draft may state (the "ground" step -- retrieved by the
        caller, e.g. via `ground()`); the brand's KB (`kb_factory(brand.id)` when a factory is
        injected, else the fixed `kb`) is used by the claim-verification guardrail to check the
        *generated* draft back against the brand's source of truth. Persists the resulting
        `(draft, report)` under `(draft.tenant_id, draft.id)` (`brand.tenant_id`, stamped by
        `generate_draft`) so the draft is later resolvable by id *within its own tenant only*.
        Returns the `DRAFT`-status draft and its `GuardrailReport` (whose `passed` gates approval).
        """
        draft = generate_draft(
            brand=brand,
            prompt_text=prompt_text,
            facts=facts,
            feature_profile=feature_profile,
            llm=self._llm,
            target_engine=target_engine,
            id_fn=self._id_fn,
        )
        report = run_guardrails(
            draft,
            kb=self._resolve_kb(brand.id),
            corpus=self._corpus,
            extractor=self._claim_extractor,
            voice_scorer=self._voice_scorer,
            voice_profile=self._voice_profile,
            thresholds=self._thresholds,
            originality_enforced=self._originality_enforced,
        )
        self._store.save(draft, report)
        # Billing metering (m4-design §4.1): one GENERATION unit per generated on-site asset,
        # recorded after the asset is saved. Only when a usage session is wired (see __init__).
        if self._usage_session is not None:
            record_usage(
                self._usage_session,
                tenant_id=draft.tenant_id,
                brand_id=draft.brand_id,
                kind=UsageKind.GENERATION,
                quantity=1,
                ts=_utcnow().isoformat(),
                source_ref=draft.id,
            )
            self._usage_session.commit()
        return draft, report

    def get_asset(
        self, *, tenant_id: str, content_id: str
    ) -> tuple[ContentDraft, GuardrailReport]:
        """Resolve the authoritative `(draft, report)` for `content_id` **within `tenant_id`**
        (the id-addressed lookup the `/content/{id}/approve|publish` endpoints run before
        enforcing the gate).

        Raises:
            LookupError: no asset was generated under `content_id` for `tenant_id` -- either it
                doesn't exist at all, or it exists under a *different* tenant. The two cases are
                deliberately indistinguishable (both **404**, mapped by the API) so a caller can
                never use this to probe for the existence of another tenant's content id.
        """
        return self._store.get(tenant_id=tenant_id, content_id=content_id)

    def approve(
        self, draft: ContentDraft, *, report: GuardrailReport, role: str, tenant_id: str
    ) -> ContentDraft:
        """Run the draft through the human approval gate (T17) and record the transition.

        `tenant_id` must match `draft.tenant_id` (checked **first**, before any state
        transition) -- a mismatch raises `LookupError`, the same "not found" a caller gets for an
        unknown id, so this holds even if a caller ever obtains a `draft` some way other than a
        tenant-scoped `get_asset`. Moves a `DRAFT` draft to `PENDING_REVIEW` first, then delegates
        to `content.approval.approve`, which raises `ApprovalError` unless **both**
        `report.passed` and `role` is an authorized reviewer. The APPROVED draft is written back
        to the store so a subsequent (separate) publish request resolves an approvable draft by
        id.
        """
        self._ensure_tenant_owns(draft, tenant_id)
        pending = (
            draft
            if draft.status == ContentStatus.PENDING_REVIEW
            else submit_for_review(draft)
        )
        approved = _approve(pending, report=report, role=role)
        self._store.save(approved, report)
        return approved

    async def publish(
        self, draft: ContentDraft, *, connector: str, tenant_id: str
    ) -> PublishResult:
        """Publish `draft` via the named connector -- but only if it is `APPROVED`.

        `tenant_id` must match `draft.tenant_id`, checked **first** -- before even
        `ensure_publishable` -- so a cross-tenant call gets the same `LookupError` (-> 404) no
        matter the draft's status, never a status-dependent response that could hint the id
        exists under another tenant. `ensure_publishable(draft)` then runs (raising
        `ApprovalError` unless the status is `APPROVED`), before the connector is resolved, so an
        unapproved draft never reaches a connector. Attaches freshness metadata
        (`datePublished`/`dateModified`) to the publish call and records the resulting
        `PUBLISHED` status (and published URL/connector) back to the store.
        """
        self._ensure_tenant_owns(draft, tenant_id)
        ensure_publishable(draft)
        conn = self._resolve_connector(connector)
        now = _utcnow().isoformat()
        result = await conn.publish(draft, freshness=freshness_meta(published=now, modified=now))
        published = draft.model_copy(update={"status": ContentStatus.PUBLISHED})
        self._store.mark_published(
            published, published_url=result.published_url, connector=result.connector
        )
        return result

    @staticmethod
    def _ensure_tenant_owns(draft: ContentDraft, tenant_id: str) -> None:
        """Raise `LookupError` unless `draft.tenant_id == tenant_id` (no cross-tenant existence
        leak: same error as an unknown id, per `get_asset`)."""
        if draft.tenant_id != tenant_id:
            raise LookupError(f"content asset {draft.id!r} not found")

    def _resolve_connector(self, name: str) -> PublishConnector:
        """Resolve a `PublishConnector` by name: the injected map first, then the shared registry.

        The injected `connectors` map is the hermetic/test path; the `publish.base` global registry
        (populated by `register_default_connectors`) is the real-wiring fallback -- i.e. the literal
        `get_connector(name)`. An unknown name raises `LookupError` (mapped to **404** by the API).
        """
        connector = self._connectors.get(name)
        if connector is not None:
            return connector
        try:
            return get_connector(name)
        except KeyError as exc:
            raise LookupError(f"unknown publish connector: {name!r}") from exc


__all__ = ["AssetStore", "ContentService", "DbAssetStore", "InMemoryAssetStore"]
