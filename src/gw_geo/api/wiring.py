"""Real, per-request providers wiring the content + opportunities services (production go-live).

:func:`gw_geo.api.app.create_app` overrides the routers' raising
``get_content_service`` / ``get_kb_factory`` / ``get_opportunity_service`` defaults with these -- the
same ``app.dependency_overrides`` seam ``leadcapture.get_db_session`` uses. Each provider builds its
service lazily, per request, from the request's DB session (``get_db_session``), the app's
``Settings`` (``get_settings_dep``), and the caller's ``Principal`` (``get_current_principal``) -- so
tenancy is always the token's, never the client's, and building performs **no I/O** (the gateway LLM
client + embedder + vector store all connect lazily, on first use, never at construction). Tests
replace these overrides with hermetic fakes, so this real path never runs under the ``not live``
suite.

Residual stubs (surfaced in the task report's CONCERNS):

* ``_NoCorpus`` -- no web/corpus-search backend is configured, so the **originality** guardrail has
  nothing to compare a draft against and passes trivially. Claim-verification (KB-grounded) and
  brand-voice are fully wired; a real :class:`~gw_geo.content.guardrails.originality.CorpusSearch`
  (e.g. ``WebCorpusSearch``) is the follow-on.
* ``voice_profile={}`` -- brands carry no persisted voice profile yet, so brand-voice scores against
  an empty profile. Persisting a per-brand voice profile is a follow-on.
* Opportunity *population* -- these providers surface/act on `Opportunity` rows a separate ranking
  worker is expected to write (via ``orchestration.opportunities.build_opportunities``); wiring that
  worker is out of scope for this API wave.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session as SASession

from gw_geo.api.auth import Principal
from gw_geo.api.deps import get_current_principal, get_db_session, get_settings_dep
from gw_geo.common.config import Settings
from gw_geo.content.gateway import (
    build_claim_extractor,
    build_kb_factory,
    build_llm_client,
    build_voice_scorer,
)
from gw_geo.content.kb import KnowledgeBase
from gw_geo.content.pipeline import ContentService, DbAssetStore
from gw_geo.content.publish.wiring import register_default_connectors
from gw_geo.orchestration.opportunity_service import DbOpportunityService


class _NoCorpus:
    """Residual: no corpus/web-search backend configured, so originality has nothing to match
    against (passes trivially). Wiring a real ``CorpusSearch`` is a follow-on."""

    def search(self, text: str, *, top_k: int = 5) -> list[tuple[str, str]]:
        return []


def build_content_service(
    *, session: SASession, settings: Settings, tenant_id: str
) -> ContentService:
    """A real, DB-backed :class:`ContentService` scoped to ``tenant_id``, built from ``settings``.

    The per-brand KB (``build_kb_factory``), LLM, claim extractor, and voice scorer come from the
    Portkey-or-direct gateway (``content.gateway``); the store is a :class:`DbAssetStore` over this
    request's session; connectors fall back to the shared registry populated by
    ``register_default_connectors`` (``hosted`` is always available). Construction is I/O-free.
    """
    register_default_connectors(settings)
    return ContentService(
        kb_factory=build_kb_factory(settings),
        llm=build_llm_client(settings),
        corpus=_NoCorpus(),
        claim_extractor=build_claim_extractor(settings),
        voice_scorer=build_voice_scorer(settings),
        voice_profile={},
        connectors={},
        store=DbAssetStore(session=session, tenant_id=tenant_id),
    )


def content_service_provider(
    session: Annotated[SASession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ContentService:
    """Per-request :class:`ContentService`, scoped to the token's tenant (`create_app` override)."""
    return build_content_service(
        session=session, settings=settings, tenant_id=principal.tenant_id
    )


def kb_factory_provider(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Callable[[str], KnowledgeBase]:
    """Per-request per-brand ``KnowledgeBase`` factory (`create_app` override for KB ingest)."""
    return build_kb_factory(settings)


def opportunity_service_provider(
    session: Annotated[SASession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DbOpportunityService:
    """Per-request :class:`DbOpportunityService`, composing a per-request `ContentService` for the
    ``act`` -> content flow (`create_app` override)."""
    content_service = build_content_service(
        session=session, settings=settings, tenant_id=principal.tenant_id
    )
    return DbOpportunityService(
        session=session, tenant_id=principal.tenant_id, content_service=content_service
    )
