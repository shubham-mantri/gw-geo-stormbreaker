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
connectors), so the whole pipeline is hermetically testable with in-memory fakes -- no live LLM /
embedding / search / HTTP call (`docs/trd.md` §12).

Persistence: `generate`/`approve`/`publish` keep the authoritative `(draft, report)` per content id
in an in-memory store, which is what the id-addressed `/content/{id}/approve|publish` endpoints
resolve against -- so the gate always runs over the *server-side* draft, never a client-supplied
one. Durable persistence to the `content_asset` + `content_guardrail_report` tables
(`gw_geo.common.db`) is the real-wiring follow-on (the service is constructed without a DB session
today, exactly like M2 left its SecretProvider wiring as a follow-on); the in-memory store is the
hermetic-fakes path called out in the task.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from gw_geo.common.models import (
    Brand,
    ContentDraft,
    ContentStatus,
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


class ContentService:
    """Compose generation + guardrails + the approval gate + publishing for one brand.

    All collaborators are injected. `connectors` is a name->`PublishConnector` map (injected fakes
    in tests); an unknown name falls back to the process-global `publish.base` registry populated by
    `publish.wiring.register_default_connectors`, so real wiring keeps working via `get_connector`.
    `thresholds=None` lets `run_guardrails` build the fail-closed thresholds from `Settings` (T01).
    """

    def __init__(
        self,
        *,
        kb: KnowledgeBase,
        llm: LLMClient,
        corpus: CorpusSearch,
        claim_extractor: ClaimExtractor,
        voice_scorer: VoiceScorer,
        voice_profile: dict[str, Any],
        connectors: Mapping[str, PublishConnector],
        thresholds: GuardrailThresholds | None = None,
        id_fn: Callable[[], str] | None = None,
    ) -> None:
        self._kb = kb
        self._llm = llm
        self._corpus = corpus
        self._claim_extractor = claim_extractor
        self._voice_scorer = voice_scorer
        self._voice_profile = voice_profile
        self._connectors = connectors
        self._thresholds = thresholds
        self._id_fn = id_fn
        # Authoritative server-side (draft, report) per content id -- the id-addressed
        # approve/publish endpoints resolve against this, never a client-supplied draft.
        self._assets: dict[str, tuple[ContentDraft, GuardrailReport]] = {}

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
        caller from the KB); the injected `kb` here is used by the claim-verification guardrail to
        check the *generated* draft back against the brand's source of truth. Persists the resulting
        `(draft, report)` under `draft.id` so the draft is later resolvable by id. Returns the
        `DRAFT`-status draft and its `GuardrailReport` (whose `passed` gates approval).
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
            kb=self._kb,
            corpus=self._corpus,
            extractor=self._claim_extractor,
            voice_scorer=self._voice_scorer,
            voice_profile=self._voice_profile,
            thresholds=self._thresholds,
        )
        self._assets[draft.id] = (draft, report)
        return draft, report

    def get_asset(self, content_id: str) -> tuple[ContentDraft, GuardrailReport]:
        """Resolve the authoritative `(draft, report)` for `content_id` (the id-addressed lookup
        the `/content/{id}/approve|publish` endpoints run before enforcing the gate).

        Raises:
            LookupError: no asset was generated under `content_id` (mapped to **404** by the API).
        """
        try:
            return self._assets[content_id]
        except KeyError as exc:
            raise LookupError(f"content asset {content_id!r} not found") from exc

    def approve(
        self, draft: ContentDraft, *, report: GuardrailReport, role: str
    ) -> ContentDraft:
        """Run the draft through the human approval gate (T17) and record the transition.

        Moves a `DRAFT` draft to `PENDING_REVIEW` first, then delegates to
        `content.approval.approve`, which raises `ApprovalError` unless **both** `report.passed`
        and `role` is an authorized reviewer. The APPROVED draft is written back to the store so a
        subsequent (separate) publish request resolves an approvable draft by id.
        """
        pending = (
            draft
            if draft.status == ContentStatus.PENDING_REVIEW
            else submit_for_review(draft)
        )
        approved = _approve(pending, report=report, role=role)
        self._assets[approved.id] = (approved, report)
        return approved

    async def publish(self, draft: ContentDraft, *, connector: str) -> PublishResult:
        """Publish `draft` via the named connector -- but only if it is `APPROVED`.

        `ensure_publishable(draft)` runs **first** (raising `ApprovalError` unless the status is
        `APPROVED`), before the connector is resolved, so an unapproved draft never reaches a
        connector. Attaches freshness metadata (`datePublished`/`dateModified`) to the publish call
        and records the resulting `PUBLISHED` status back to the store.
        """
        ensure_publishable(draft)
        conn = self._resolve_connector(connector)
        now = datetime.now(timezone.utc).isoformat()
        result = await conn.publish(draft, freshness=freshness_meta(published=now, modified=now))
        published = draft.model_copy(update={"status": ContentStatus.PUBLISHED})
        if draft.id in self._assets:
            self._assets[draft.id] = (published, self._assets[draft.id][1])
        return result

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


__all__ = ["ContentService"]
