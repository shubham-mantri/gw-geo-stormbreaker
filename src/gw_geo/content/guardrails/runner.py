"""Guardrail runner: the single choke point (`docs/prd.md` §6.4, §13; `docs/trd.md`).

Aggregates the three independent guardrails -- originality/plagiarism (T07,
`gw_geo.content.guardrails.originality.check_originality`), claim-verification against the brand
knowledge base (T15, `gw_geo.content.guardrails.claims.verify_claims`), and brand-voice
conformance (T08, `gw_geo.content.guardrails.brand_voice.check_brand_voice`) -- into one
`GuardrailReport`. Its `passed` flag is the hard precondition the human approval gate (T17,
`gw_geo.content.approval.approve`) and publish (T22) both key off of:

    passed = originality_ok AND claims_ok AND brand_voice_ok

Fail-closed by composition: since each of the three underlying checks is itself fail-closed (a
tie at a threshold blocks, never passes), and `passed` is a strict `AND` with no override, a
single failure -- a near-duplicate draft, a fabricated/ungrounded claim, or off-brand-voice copy
-- forces `passed = False`. There is no partial-credit path and no way to route around this
function to reach approval/publish. This is what makes Athena's documented failure (a
plagiarized, fabricated, unreviewed draft reaching publish) structurally impossible here.

`run_guardrails` is pure orchestration: it carries no scoring logic of its own beyond the `AND`,
so it is exercised in the hermetic unit tests via the same kind of in-memory fakes the three
underlying guardrails already use -- no live LLM/embedding/search calls (`docs/trd.md` §12).
`thresholds` defaults to `None`, in which case `GuardrailThresholds` is built from `Settings`
(T01, via `get_settings()`), so a caller that never overrides thresholds gets the fail-closed
config defaults rather than a copy hardcoded here that could drift from `Settings`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gw_geo.common.config import get_settings
from gw_geo.common.models import ContentDraft, GuardrailReport
from gw_geo.content.guardrails.brand_voice import VoiceScorer, check_brand_voice
from gw_geo.content.guardrails.claims import ClaimExtractor, verify_claims
from gw_geo.content.guardrails.originality import CorpusSearch, check_originality
from gw_geo.content.kb import KnowledgeBase


@dataclass
class GuardrailThresholds:
    """Per-guardrail thresholds, mirroring `Settings`' M3 guardrail defaults (T01).

    These field defaults match `Settings.originality_threshold` / `.claim_sim_threshold` /
    `.brand_voice_min` as of T01; `run_guardrails` itself does not rely on these dataclass
    defaults when `thresholds` is omitted -- it builds a `GuardrailThresholds` from
    `get_settings()` instead, so config remains the single source of truth. These field defaults
    exist for callers that construct `GuardrailThresholds` directly (e.g. to override just one
    field while matching config for the others).
    """

    originality: float = 0.25
    claim_sim: float = 0.8
    brand_voice: float = 0.7


def run_guardrails(
    draft: ContentDraft,
    *,
    kb: KnowledgeBase,
    corpus: CorpusSearch,
    extractor: ClaimExtractor,
    voice_scorer: VoiceScorer,
    voice_profile: dict[str, Any],
    thresholds: GuardrailThresholds | None = None,
) -> GuardrailReport:
    """Run all three guardrails against `draft.body_markdown` and compose one `GuardrailReport`.

    Calls, in order, `check_originality` (against `corpus`), `verify_claims` (claims pulled by
    `extractor`, grounded against `kb`), and `check_brand_voice` (scored by `voice_scorer` against
    `voice_profile`) -- each with its threshold taken from `thresholds`. `thresholds=None` (the
    default) builds thresholds from `get_settings()` (T01), so omitting it means "use the
    fail-closed config defaults", not "use no threshold".

    Returns:
        A `GuardrailReport` whose `passed` is `True` iff `originality_ok AND claims_ok AND
        brand_voice_ok` -- any single failing guardrail forces `passed=False`.
    """
    if thresholds is None:
        settings = get_settings()
        thresholds = GuardrailThresholds(
            originality=settings.originality_threshold,
            claim_sim=settings.claim_sim_threshold,
            brand_voice=settings.brand_voice_min,
        )

    originality_ok, originality_score, _matched_urls = check_originality(
        draft.body_markdown, corpus=corpus, threshold=thresholds.originality
    )
    claims_ok, unverified_claims = verify_claims(
        draft.body_markdown, kb=kb, extractor=extractor, sim_threshold=thresholds.claim_sim
    )
    brand_voice_ok, brand_voice_score, _violations = check_brand_voice(
        draft.body_markdown, voice_profile, scorer=voice_scorer, min_score=thresholds.brand_voice
    )

    return GuardrailReport(
        originality_ok=originality_ok,
        originality_score=originality_score,
        claims_ok=claims_ok,
        unverified_claims=unverified_claims,
        brand_voice_ok=brand_voice_ok,
        brand_voice_score=brand_voice_score,
        passed=originality_ok and claims_ok and brand_voice_ok,
    )
