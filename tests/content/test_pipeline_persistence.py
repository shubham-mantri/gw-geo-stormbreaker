"""Tests for the DB-backed `AssetStore` seam + per-brand `kb_factory` on `ContentService`
(production go-live wave 1).

These cover the two new seams that make the `/content/*` HTTP endpoints usable across separate
requests:

* :class:`gw_geo.content.pipeline.DbAssetStore` -- persists `(draft, GuardrailReport)` to the
  `content_asset` + `content_guardrail_report` tables and reloads them (statuses included), so a
  generate in one request is resolvable by an approve/publish in the next. Hermetic: an in-memory
  SQLite engine (shared via `StaticPool`), never Postgres.
* the optional `kb_factory` -- when injected, `generate` grounds + claim-verifies against
  `kb_factory(brand.id)` (a per-brand KB) instead of a single fixed `self._kb`.

The default (no `store`/`kb_factory`) path stays :class:`InMemoryAssetStore` + fixed `kb`, exercised
byte-identically by `tests/content/test_pipeline.py`.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session as SASession
from sqlalchemy.pool import StaticPool

from gw_geo.common.db import Base, Brand as BrandRow, ContentAsset, Tenant
from gw_geo.common.models import (
    Brand,
    ContentDraft,
    ContentStatus,
    Fact,
    GuardrailReport,
)
from gw_geo.content.kb import KnowledgeBase
from gw_geo.content.pipeline import ContentService, DbAssetStore, InMemoryAssetStore
from gw_geo.content.publish.base import PublishResult
from tests.content.guardrails.test_claims import FakeStore, WordEmbedder

_BRAND = Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com")


def _engine() -> Engine:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    with SASession(eng) as s:
        s.add(Tenant(id="t1", name="Acme", sampling_budget_daily=100.0))
        s.add(BrandRow(id="b1", tenant_id="t1", name="Acme", domain="acme.com"))
        s.commit()
    return eng


def _draft(content_id: str = "c1", status: ContentStatus = ContentStatus.DRAFT) -> ContentDraft:
    return ContentDraft(
        id=content_id,
        tenant_id="t1",
        brand_id="b1",
        target_engine="perplexity",
        intent_cluster="evaluation",
        title="Best CRM",
        body_markdown="## Answer\nAcme is great.",
        schema_jsonld={"@type": "FAQPage"},
        grounded_fact_ids=["f1", "f2"],
        status=status,
    )


def _report(passed: bool = True) -> GuardrailReport:
    return GuardrailReport(
        originality_ok=passed,
        originality_score=0.1,
        claims_ok=passed,
        unverified_claims=[] if passed else ["fabricated"],
        brand_voice_ok=passed,
        brand_voice_score=0.9,
        passed=passed,
    )


# --------------------------------------------------------------------------------------------
# DbAssetStore
# --------------------------------------------------------------------------------------------


def test_db_store_roundtrips_draft_and_report() -> None:
    eng = _engine()
    with SASession(eng) as session:
        store = DbAssetStore(session=session, tenant_id="t1")
        store.save(_draft(), _report())

    # A brand-new session (fresh identity map) must reload the exact draft + report from the DB.
    with SASession(eng) as session:
        got_draft, got_report = DbAssetStore(session=session, tenant_id="t1").get(
            tenant_id="t1", content_id="c1"
        )
    assert got_draft.id == "c1"
    assert got_draft.body_markdown == "## Answer\nAcme is great."
    assert got_draft.intent_cluster == "evaluation"
    assert got_draft.target_engine == "perplexity"
    assert got_draft.grounded_fact_ids == ["f1", "f2"]
    assert got_draft.schema_jsonld == {"@type": "FAQPage"}
    assert got_draft.status == ContentStatus.DRAFT
    assert got_report.passed is True
    assert got_report.originality_score == 0.1


def test_db_store_persists_status_transition() -> None:
    eng = _engine()
    with SASession(eng) as session:
        store = DbAssetStore(session=session, tenant_id="t1")
        store.save(_draft(), _report())
        # re-save with an APPROVED draft (what ContentService.approve does)
        store.save(_draft(status=ContentStatus.APPROVED), _report())

    with SASession(eng) as session:
        got_draft, _ = DbAssetStore(session=session, tenant_id="t1").get(
            tenant_id="t1", content_id="c1"
        )
    assert got_draft.status == ContentStatus.APPROVED


def test_db_store_mark_published_records_url_and_status() -> None:
    eng = _engine()
    with SASession(eng) as session:
        store = DbAssetStore(session=session, tenant_id="t1")
        store.save(_draft(status=ContentStatus.APPROVED), _report())
        published = _draft(status=ContentStatus.PUBLISHED)
        store.mark_published(
            published, published_url="https://kb.example.com/b1/c1", connector="hosted"
        )

    with SASession(eng) as session:
        got_draft, _ = DbAssetStore(session=session, tenant_id="t1").get(
            tenant_id="t1", content_id="c1"
        )
        asset = session.get(ContentAsset, "c1")
    assert got_draft.status == ContentStatus.PUBLISHED
    assert asset is not None
    assert asset.published_url == "https://kb.example.com/b1/c1"
    assert asset.connector == "hosted"
    assert asset.published_at is not None


def test_db_store_unknown_id_raises_lookup_error() -> None:
    eng = _engine()
    with SASession(eng) as session:
        with pytest.raises(LookupError):
            DbAssetStore(session=session, tenant_id="t1").get(tenant_id="t1", content_id="nope")


def test_db_store_wrong_tenant_raises_lookup_error() -> None:
    # An id that exists but under another tenant must 404 exactly like an unknown id (no IDOR).
    eng = _engine()
    with SASession(eng) as session:
        DbAssetStore(session=session, tenant_id="t1").save(_draft(), _report())
    with SASession(eng) as session:
        with pytest.raises(LookupError):
            DbAssetStore(session=session, tenant_id="t2").get(tenant_id="t2", content_id="c1")


# --------------------------------------------------------------------------------------------
# ContentService wired to a DB-backed store (cross-"request" persistence)
# --------------------------------------------------------------------------------------------


class _NullVectorStore:
    def upsert(self, id: str, vector: list[float], meta: dict[str, Any]) -> None:
        pass

    def query(self, vector: list[float], top_k: int) -> list[tuple[str, float, dict[str, Any]]]:
        return []


class _NullEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.0]


class _NullCorpus:
    def search(self, text: str, *, top_k: int = 5) -> list[tuple[str, str]]:
        return []


class _NoClaims:
    def extract_claims(self, text: str) -> list[str]:
        return []


class _GoodVoice:
    def score(self, text: str, voice_profile: dict[str, Any]) -> dict[str, Any]:
        return {"score": 1.0, "violations": []}


class _StubLLM:
    def complete(
        self, *, system: str, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {"title": "T", "body_markdown": "hello world", "schema_jsonld": {}}


class _FakeConnector:
    name = "hosted"

    async def publish(self, draft: ContentDraft, *, freshness: dict[str, Any]) -> PublishResult:
        return PublishResult(
            published_url=f"https://kb.example.com/{draft.brand_id}/{draft.id}",
            external_id=f"ext-{draft.id}",
            connector=self.name,
        )


def _service(session: SASession, tenant_id: str = "t1") -> ContentService:
    """A `ContentService` whose store is DB-backed over `session` and whose KB is per-brand."""
    return ContentService(
        kb_factory=lambda bid: KnowledgeBase(
            brand_id=bid, store=_NullVectorStore(), embedder=_NullEmbedder()
        ),
        llm=_StubLLM(),
        corpus=_NullCorpus(),
        claim_extractor=_NoClaims(),
        voice_scorer=_GoodVoice(),
        voice_profile={},
        connectors={"hosted": _FakeConnector()},
        store=DbAssetStore(session=session, tenant_id=tenant_id),
        id_fn=lambda: "c1",
    )


@pytest.mark.asyncio
async def test_generate_approve_publish_span_separate_stores_via_db() -> None:
    """generate -> approve -> publish each build a *fresh* `ContentService` (fresh in-memory
    identity), sharing only the DB -- so the flow only works if state is persisted to the DB, not
    held in a per-instance in-memory dict. This is the HTTP reality the endpoints face."""
    eng = _engine()

    with SASession(eng) as session:
        draft, report = _service(session).generate(
            brand=_BRAND, prompt_text="best crm", facts=[], feature_profile=None
        )
    assert draft.id == "c1"
    assert report.passed is True

    # A separate "request": brand-new session + service, no shared memory.
    with SASession(eng) as session:
        svc = _service(session)
        d, r = svc.get_asset(tenant_id="t1", content_id="c1")
        approved = svc.approve(d, report=r, role="editor", tenant_id="t1")
    assert approved.status == ContentStatus.APPROVED

    # A third "request": publish must resolve the APPROVED draft from the DB.
    with SASession(eng) as session:
        svc = _service(session)
        d, _ = svc.get_asset(tenant_id="t1", content_id="c1")
        result = await svc.publish(d, connector="hosted", tenant_id="t1")
    assert result.published_url == "https://kb.example.com/b1/c1"

    with SASession(eng) as session:
        asset = session.get(ContentAsset, "c1")
    assert asset is not None
    assert asset.status == ContentStatus.PUBLISHED.value


# --------------------------------------------------------------------------------------------
# Per-brand kb_factory
# --------------------------------------------------------------------------------------------


def _brand_kb() -> KnowledgeBase:
    kb = KnowledgeBase(brand_id="b1", store=FakeStore(), embedder=WordEmbedder())
    kb.add_fact(Fact(id="f1", brand_id="b1", text="Acme is soc2 certified", category="cert"))
    return kb


def test_ground_uses_kb_factory() -> None:
    svc = ContentService(
        kb_factory=lambda bid: _brand_kb(),
        llm=_StubLLM(),
        corpus=_NullCorpus(),
        claim_extractor=_NoClaims(),
        voice_scorer=_GoodVoice(),
        voice_profile={},
        connectors={},
    )
    facts = svc.ground(brand_id="b1", prompt_text="soc2 certification")
    assert [f.id for f in facts] == ["f1"]


class _Soc2Extractor:
    def extract_claims(self, text: str) -> list[str]:
        return ["Acme is soc2 certified"]


def test_generate_verifies_claims_against_kb_factory_not_fixed_kb() -> None:
    # The claim IS grounded in the per-brand KB the factory builds; if generate wrongly used a
    # fixed empty `self._kb`, the claim would be unverified and `claims_ok` would be False.
    svc = ContentService(
        kb_factory=lambda bid: _brand_kb(),
        llm=_StubLLM(),
        corpus=_NullCorpus(),
        claim_extractor=_Soc2Extractor(),
        voice_scorer=_GoodVoice(),
        voice_profile={},
        connectors={},
    )
    _, report = svc.generate(
        brand=_BRAND, prompt_text="best crm", facts=[], feature_profile=None
    )
    assert report.claims_ok is True


def test_requires_kb_or_kb_factory() -> None:
    with pytest.raises(ValueError):
        ContentService(
            llm=_StubLLM(),
            corpus=_NullCorpus(),
            claim_extractor=_NoClaims(),
            voice_scorer=_GoodVoice(),
            voice_profile={},
            connectors={},
        )


def test_in_memory_store_is_the_default() -> None:
    svc = ContentService(
        kb=_brand_kb(),
        llm=_StubLLM(),
        corpus=_NullCorpus(),
        claim_extractor=_NoClaims(),
        voice_scorer=_GoodVoice(),
        voice_profile={},
        connectors={},
    )
    assert isinstance(svc._store, InMemoryAssetStore)  # noqa: SLF001 (documenting the default)
