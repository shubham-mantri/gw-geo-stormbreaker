"""Tests for the on-site content pipeline (M3-T22, m3-design §3.6).

The `ContentService` composes generation (T14) + guardrails (T16) + the approval gate (T17) +
publishing connectors (T09/T18) into one ``ground -> generate -> run_guardrails -> (gate) ->
publish`` flow. These tests assert the honesty gate holds by construction:

* a failing `GuardrailReport` blocks approval (``ApprovalError``), and
* ``publish`` refuses any draft that is not ``APPROVED`` -- the ``ensure_publishable`` check runs
  *before* any connector is even resolved, so an unapproved draft can never reach a connector.

Hermetic: every collaborator (LLM, corpus, claim extractor, voice scorer, KB store/embedder,
publish connector) is an in-memory fake -- no live LLM/embedding/search/HTTP call. Reuses the
``WordEmbedder``/``FakeStore`` KB fakes from the T15 claim-verification tests so the KB grounds
exactly as the guardrail suite already proves.
"""

from __future__ import annotations

from typing import Any

import pytest

from gw_geo.common.models import (
    Brand,
    ContentDraft,
    ContentStatus,
    Fact,
    GuardrailReport,
)
from gw_geo.content.approval import ApprovalError
from gw_geo.content.kb import KnowledgeBase
from gw_geo.content.pipeline import ContentService
from gw_geo.content.publish.base import PublishResult
from tests.content.guardrails.test_claims import FakeStore, WordEmbedder

# The single body every StubLLM emits. `DupCorpus` returns this verbatim so the originality
# guardrail sees a ~1.0 Jaccard duplicate (blocks); `CleanCorpus` finds nothing (passes).
_BODY = "## Answer\nAcme is soc2 certified. Plans start at $29 per month for small teams."

_BRAND = Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com")


class StubLLM:
    """Injected `LLMClient` that returns a fixed grounded draft (never a live call)."""

    def complete(
        self, *, system: str, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "title": "Best CRM for Startups",
            "body_markdown": _BODY,
            "schema_jsonld": {"@type": "FAQPage"},
        }


class CleanCorpus:
    """Nothing similar in the corpus -> original."""

    def search(self, text: str, *, top_k: int = 5) -> list[tuple[str, str]]:
        return []


class DupCorpus:
    """The closest corpus hit is the draft body itself -> plagiarized (originality blocks)."""

    def search(self, text: str, *, top_k: int = 5) -> list[tuple[str, str]]:
        return [("https://dup.example.com", _BODY)]


class GroundedExtractor:
    """Extracts a claim that IS backed by the KB fact -> claims verify."""

    def extract_claims(self, text: str) -> list[str]:
        return ["Acme is soc2 certified"]


class GoodVoice:
    def score(self, text: str, voice_profile: dict[str, Any]) -> dict[str, Any]:
        return {"score": 0.95, "violations": []}


class FakeConnector:
    """In-memory `PublishConnector`: records what it published, returns a deterministic URL."""

    name = "hosted"

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(
        self, draft: ContentDraft, *, freshness: dict[str, Any]
    ) -> PublishResult:
        self.published.append((draft.id, freshness))
        return PublishResult(
            published_url=f"https://kb.example.com/{draft.brand_id}/{draft.id}",
            external_id=f"ext-{draft.id}",
            connector=self.name,
        )


def _kb() -> KnowledgeBase:
    kb = KnowledgeBase(brand_id="b1", store=FakeStore(), embedder=WordEmbedder())
    kb.add_fact(
        Fact(id="f1", brand_id="b1", text="Acme is soc2 certified", category="certification")
    )
    return kb


def _service(*, passing: bool = True, connector: FakeConnector | None = None) -> ContentService:
    """Wire a `ContentService` from grounded fakes.

    ``passing=True`` -> a clean corpus so originality/claims/voice all pass. ``passing=False`` ->
    a `DupCorpus` whose only hit is the draft body verbatim, so the originality guardrail blocks
    (``passed=False``), exactly the plagiarism failure the gate must catch.
    """
    return ContentService(
        kb=_kb(),
        llm=StubLLM(),
        corpus=CleanCorpus() if passing else DupCorpus(),
        claim_extractor=GroundedExtractor(),
        voice_scorer=GoodVoice(),
        voice_profile={},
        connectors={"hosted": connector if connector is not None else FakeConnector()},
        id_fn=lambda: "c1",
    )


@pytest.mark.asyncio
async def test_generate_then_gate_then_publish() -> None:
    conn = FakeConnector()
    svc = _service(passing=True, connector=conn)
    draft, report = svc.generate(
        brand=_BRAND,
        prompt_text="best crm",
        facts=[Fact(id="f1", brand_id="b1", text="Acme is soc2 certified")],
        feature_profile=None,
    )
    assert report.passed is True
    assert report.originality_ok and report.claims_ok and report.brand_voice_ok
    assert draft.status == ContentStatus.DRAFT

    approved = svc.approve(draft, report=report, role="editor")
    assert approved.status == ContentStatus.APPROVED

    result = await svc.publish(approved, connector="hosted")
    assert isinstance(result, PublishResult)
    assert result.connector == "hosted"
    assert result.published_url == "https://kb.example.com/b1/c1"
    # The connector was actually invoked with a freshness dict (datePublished/dateModified).
    assert conn.published and set(conn.published[0][1]) == {"datePublished", "dateModified"}


@pytest.mark.asyncio
async def test_publish_blocked_before_approval() -> None:
    svc = _service(passing=True)
    d = ContentDraft(id="c1", tenant_id="t1", brand_id="b1", title="T", body_markdown="x")
    # Never approved (status defaults to DRAFT) -> the gate blocks before any connector is touched.
    with pytest.raises(ApprovalError):
        await svc.publish(d, connector="hosted")


@pytest.mark.asyncio
async def test_publish_gate_runs_before_connector_lookup() -> None:
    # Even with an unregistered connector name, the approval gate fails first (ApprovalError, not
    # a connector KeyError/LookupError): nothing about the target is reachable without approval.
    svc = _service(passing=True)
    d = ContentDraft(id="c9", tenant_id="t1", brand_id="b1", title="T", body_markdown="x")
    with pytest.raises(ApprovalError):
        await svc.publish(d, connector="no-such-connector")


def test_failing_guardrails_block_approval() -> None:
    svc = _service(passing=False)
    draft, report = svc.generate(
        brand=_BRAND, prompt_text="best crm", facts=[], feature_profile=None
    )
    assert report.passed is False
    assert report.originality_ok is False  # plagiarism caught
    with pytest.raises(ApprovalError):
        svc.approve(draft, report=report, role="editor")


def test_clean_report_but_unauthorized_role_blocks_approval() -> None:
    # Both conditions are required: a passing report from a `viewer` is still refused (the gate is
    # RBAC + guardrails, never one alone).
    svc = _service(passing=True)
    draft, report = svc.generate(
        brand=_BRAND, prompt_text="best crm", facts=[], feature_profile=None
    )
    assert report.passed is True
    with pytest.raises(ApprovalError):
        svc.approve(draft, report=report, role="viewer")


def test_generate_persists_asset_for_id_lookup() -> None:
    # `generate` stores the (draft, report) so the id-addressed /approve + /publish endpoints can
    # resolve the *server-side* authoritative draft (never a client-supplied one).
    svc = _service(passing=True)
    draft, report = svc.generate(
        brand=_BRAND, prompt_text="best crm", facts=[], feature_profile=None
    )
    got_draft, got_report = svc.get_asset(draft.id)
    assert got_draft.id == draft.id
    assert got_report.passed == report.passed


def test_get_asset_unknown_id_raises_lookup_error() -> None:
    svc = _service(passing=True)
    with pytest.raises(LookupError):
        svc.get_asset("nope")


def test_approve_records_transition_so_publish_sees_approved() -> None:
    # After approve, the stored asset reflects APPROVED, so a later publish (a separate request in
    # the API) resolves an approvable draft by id.
    svc = _service(passing=True)
    draft, report = svc.generate(
        brand=_BRAND, prompt_text="best crm", facts=[], feature_profile=None
    )
    svc.approve(draft, report=report, role="editor")
    stored_draft, _ = svc.get_asset(draft.id)
    assert stored_draft.status == ContentStatus.APPROVED


def test_generate_returns_guardrail_report_type() -> None:
    svc = _service(passing=True)
    _, report = svc.generate(
        brand=_BRAND, prompt_text="best crm", facts=[], feature_profile=None
    )
    assert isinstance(report, GuardrailReport)
