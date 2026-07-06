"""Local off-site-seeding discovery trigger (m4 seeding live-wiring) -- HUMAN-EXECUTED, no poster.

This is the missing wiring that lets the seeding subsystem run end-to-end from live measurement
data locally: it composes the AnswerExtraction-backed `CitationSourceMap` (this package's
`sourcemap.py`) with the pure `discovery.discover_targets`, the `SeedingWorkflow` state machine, and
-- optionally -- grounded `briefs.build_brief` drafting through `PortkeyBriefLLM`. It mirrors
`attribution.trigger` / `orchestration.ranking_gen`: a hermetic **core** that takes every I/O
collaborator injected (so tests pass fakes and make no live DB-source / LLM / network call), plus a
local, in-process **job** that owns its own `Session` and wires the real collaborators.

**White-hat, human-in-the-loop (PRD NG1). There is deliberately NO auto-poster and no network to any
platform.** The chain this drives stops at, at most, `briefed`:

    discover targets -> create `todo` task -> (optionally) attach a grounded brief -> `briefed`

It never calls `SeedingWorkflow.run_compliance` and never calls `mark_placed`, so every task it
produces is left with `compliance_status="pending"`, an empty `compliance_report`, and no
`placed_url`/`actor`. Running the compliance gate, and the human-only, gated `mark_placed`
transition, remain separate steps a person performs after reviewing the brief -- this job produces
review-ready work, not published placements.

Briefing is **optional and config-gated**: the job drafts briefs only when a chat LLM is configured
(Portkey gateway keyed, or a direct Anthropic key); with no key it still discovers targets and opens
`todo` tasks, so the whole chain runs fully local with zero external dependency. `get_settings` /
`build_llm_client` / `build_kb_factory` are imported by name so tests can patch them on this module.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm import Session as SASession

from gw_geo.common.config import Settings, get_settings
from gw_geo.common.db import Brand
from gw_geo.content.gateway import build_kb_factory, build_llm_client
from gw_geo.content.kb import KnowledgeBase
from gw_geo.seeding.brief_llm import PortkeyBriefLLM
from gw_geo.seeding.briefs import BriefLLM, build_brief
from gw_geo.seeding.channels import ChannelCatalog, load_catalog
from gw_geo.seeding.compliance import ComplianceEngine
from gw_geo.seeding.discovery import SourceMap, discover_targets
from gw_geo.seeding.sourcemap import CitationSourceMap
from gw_geo.seeding.workflow import SeedingWorkflow

logger = logging.getLogger(__name__)

# Default discovery look-back when the caller names no window: the trailing 90 days (inclusive) --
# the same convention as `attribution.trigger`.
_LOOKBACK_DAYS = 90
# How many brand-KB facts to ground per brand for brief drafting.
_FACT_TOP_K = 8


def _default_window() -> tuple[str, str]:
    """`(since, until)` ISO dates for the default trailing `_LOOKBACK_DAYS`-day window."""
    until = datetime.now(timezone.utc).date()
    since = until - timedelta(days=_LOOKBACK_DAYS - 1)
    return since.isoformat(), until.isoformat()


def _llm_configured(settings: Settings) -> bool:
    """True iff a chat LLM is available (local Claude subscription, Portkey keyed, or Anthropic key).

    Mirrors `content.gateway.build_llm_client`'s own routing: the local `claude -p` gateway needs
    no key (Claude Max subscription), Portkey needs `portkey_api_key`, and direct needs
    `anthropic_api_key`. When none is present, briefing is skipped and discovery still opens `todo`
    tasks -- fully local, no gateway.
    """
    if settings.llm_gateway == "local_claude":
        return True
    if settings.llm_gateway == "portkey" and settings.portkey_api_key:
        return True
    return bool(settings.anthropic_api_key)


def _facts_query(brand: Brand) -> str:
    """The grounding query used to pull seed-worthy brand-KB facts for a brief."""
    return (
        f"key differentiators, proof points, and value propositions of {brand.name} "
        "suitable for a genuine, disclosed off-site contribution"
    )


def run_seeding_discovery(
    *,
    session: SASession,
    tenant_id: str,
    brand_id: str,
    since: str,
    until: str,
    source_map: SourceMap,
    channels: ChannelCatalog,
    brief_llm: BriefLLM | None = None,
    kb_factory: Callable[[str], KnowledgeBase] | None = None,
    compliance_engine: ComplianceEngine | None = None,
    budget: int | None = None,
) -> int:
    """Discover seeding targets for `brand_id`, open a `todo` task per target, optionally brief them.

    Hermetic core: `source_map` (the citation-source reader), `brief_llm`, and `kb_factory` are all
    injected. Ranks targets via `discover_targets` (highest-priority first, capped to `budget` when
    given), then for each creates a `SeedingWorkflow` task in `todo`. When `brief_llm` is provided, a
    grounded `SeedingBrief` (facts pulled from the brand KB via `kb_factory`, then verbatim-filtered
    by `build_brief`) is attached, advancing the task to `briefed`. **Never runs compliance, never
    places, never posts** -- see the module docstring. A missing or cross-tenant brand is a no-op
    returning `0` (mirrors `attribution.trigger` / `orchestration.ranking_gen`). Returns the number
    of tasks created.
    """
    brand = session.get(Brand, brand_id)
    if brand is None or brand.tenant_id != tenant_id:
        logger.warning(
            "brand_id=%r not found for tenant_id=%r; no seeding targets discovered",
            brand_id,
            tenant_id,
        )
        return 0

    if budget is not None:
        targets = discover_targets(
            source_map, tenant_id=tenant_id, brand_id=brand_id, since=since, until=until,
            channels=channels, limit=budget,
        )
    else:
        targets = discover_targets(
            source_map, tenant_id=tenant_id, brand_id=brand_id, since=since, until=until,
            channels=channels,
        )

    # Facts for briefing are pulled once per brand (they are brand-level, not per-target).
    facts: list[str] = []
    if brief_llm is not None and kb_factory is not None:
        grounded = kb_factory(brand_id).ground(_facts_query(brand), top_k=_FACT_TOP_K)
        facts = [fact.text for fact in grounded]

    engine = compliance_engine or ComplianceEngine(ComplianceEngine.default_ruleset())
    workflow = SeedingWorkflow(session, tenant_id, engine)

    created = 0
    for target in targets:
        task_id = workflow.create(brand_id=brand_id, channel=target.channel)
        if brief_llm is not None:
            brief = build_brief(
                brief_llm, target=target, facts=facts, channel=channels.get(target.channel)
            )
            workflow.attach_brief(task_id, brief)
        created += 1

    logger.info(
        "seeding discovery tenant_id=%s brand_id=%s window=%s..%s targets=%d briefed=%s",
        tenant_id,
        brand_id,
        since,
        until,
        created,
        brief_llm is not None,
    )
    return created


def run_seeding_discovery_job(
    *,
    tenant_id: str,
    brand_id: str,
    since: str | None = None,
    until: str | None = None,
    budget: int | None = None,
) -> int:
    """Local, in-process seeding discovery for `brand_id`; opens (and always closes) its own session.

    The single unit both the CLI `seed-discover` subcommand and any future request path call. A plain
    sync function that owns its `Session` (built from `settings.database_url`) and wires the real
    collaborators: the AnswerExtraction-backed `CitationSourceMap`, the persisted active channel
    catalog (`load_catalog`), and -- only when a chat LLM is configured -- `PortkeyBriefLLM` +
    per-brand KB grounding for brief drafting. No AWS/Lambda/EventBridge, and NO poster: it stops at
    `todo`/`briefed` (see the module docstring). `since`/`until` default to the trailing
    `_LOOKBACK_DAYS`-day window when omitted. Returns the number of tasks created. `get_settings` /
    `build_llm_client` / `build_kb_factory` are imported by name so tests can patch them here and
    keep the job hermetic.
    """
    settings = get_settings()
    if since is None or until is None:
        default_since, default_until = _default_window()
        since = since or default_since
        until = until or default_until

    brief_llm: BriefLLM | None = None
    kb_factory: Callable[[str], KnowledgeBase] | None = None
    if _llm_configured(settings):
        brief_llm = PortkeyBriefLLM(build_llm_client(settings))
        kb_factory = build_kb_factory(settings)

    engine = create_engine(settings.database_url)
    session = Session(engine)
    try:
        count = run_seeding_discovery(
            session=session,
            tenant_id=tenant_id,
            brand_id=brand_id,
            since=since,
            until=until,
            source_map=CitationSourceMap(session),
            channels=load_catalog(session),
            brief_llm=brief_llm,
            kb_factory=kb_factory,
            budget=budget,
        )
    finally:
        session.close()

    logger.info(
        "seeding discovery job done tenant_id=%s brand_id=%s tasks=%d", tenant_id, brand_id, count
    )
    return count


__all__ = ["run_seeding_discovery", "run_seeding_discovery_job"]
