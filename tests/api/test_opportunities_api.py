"""Tests for the `/opportunities` endpoints (M3-T21, ui-spec.md §3.4/§6).

Reconciled to M2's API (M3-T10 was skipped): the opportunities router mounts into M2's
``create_app`` and reuses M2's ``get_current_principal``/``require_role``. ``OpportunityService``
is injected via the overridable ``opportunities.get_opportunity_service`` dependency (default
raises), so these tests stub it with ``app.dependency_overrides[opportunities.get_opportunity_service]``
-- mirroring ``tests/api/test_content_api.py``'s ``get_content_service`` idiom, not the stale
``create_app(Services(...))``/``get_principal`` snippet in the task spec. No live DB/ranking/LLM
call.

Fixtures (``app_client``, ``make_token``) live in ``tests/api/conftest.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session as SASession

from gw_geo.api import deps
from gw_geo.api.auth import Principal
from gw_geo.api.routers import opportunities
from gw_geo.common.db import Brand as BrandRow
from gw_geo.common.db import ContentAsset, Opportunity, Tenant
from gw_geo.content.kb import KnowledgeBase
from gw_geo.content.pipeline import ContentService, DbAssetStore
from gw_geo.content.publish.base import PublishResult
from gw_geo.orchestration.opportunity_service import DbOpportunityService


class StubOpps:
    """A stub `OpportunityService`: returns fixtures, never touches the DB/ranking/content pipeline."""

    def list_for_brand(self, *, tenant_id: str, brand_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": "o1",
                "title": "absent on Gemini",
                "rationale": "0% mention",
                "est_impact": 0.9,
                "engine": "gemini",
            }
        ]

    def act(self, *, tenant_id: str, opportunity_id: str) -> dict[str, Any]:
        return {"content_id": "c1"}


def _wire(client: TestClient, stub: StubOpps) -> TestClient:
    """Point the app's `get_opportunity_service` at `stub` (the injected-service test seam)."""
    client.app.dependency_overrides[opportunities.get_opportunity_service] = lambda: stub
    return client


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_list_opportunities_uispec_shape(
    app_client: TestClient, make_token: Callable[..., str]
) -> None:
    client = _wire(app_client, StubOpps())
    r = client.get("/brands/b1/opportunities", headers=_auth(make_token(role="viewer")))
    assert r.status_code == 200
    row = r.json()[0]
    assert set(row) == {"id", "title", "rationale", "est_impact", "engine"}  # ui-spec §6


def test_list_opportunities_requires_auth(app_client: TestClient) -> None:
    client = _wire(app_client, StubOpps())
    r = client.get("/brands/b1/opportunities")
    assert r.status_code == 401  # no bearer token


def test_act_spawns_content(app_client: TestClient, make_token: Callable[..., str]) -> None:
    client = _wire(app_client, StubOpps())
    r = client.post("/opportunities/o1/act", json={}, headers=_auth(make_token(role="editor")))
    assert r.status_code == 200
    assert r.json() == {"content_id": "c1"}  # ui-spec §6: {content_id}


def test_act_requires_editor_role(
    app_client: TestClient, make_token: Callable[..., str]
) -> None:
    client = _wire(app_client, StubOpps())
    r = client.post("/opportunities/o1/act", json={}, headers=_auth(make_token(role="viewer")))
    assert r.status_code == 403  # RBAC gate (ui-spec §5): viewer cannot act


# --------------------------------------------------------------------------------------------
# Real `DbOpportunityService` over SQLite: `list_for_brand` reads persisted rows, and `act`
# spawns a real content draft (persisted to `content_asset`) through the content pipeline.
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


class _NullClaimExtractor:
    def extract_claims(self, text: str) -> list[str]:
        return []


class _NullVoiceScorer:
    def score(self, text: str, voice_profile: dict[str, Any]) -> dict[str, Any]:
        return {"score": 1.0, "violations": []}


class _NullLLM:
    def complete(
        self, *, system: str, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {"title": "T", "body_markdown": "hello world", "schema_jsonld": {}}


class _NullConnector:
    name = "hosted"

    async def publish(self, draft: Any, *, freshness: dict[str, Any]) -> PublishResult:
        return PublishResult(
            published_url="https://kb.example.com/x", external_id="e", connector=self.name
        )


def _seed_opportunity(engine: Engine, *, tenant_id: str = "t1") -> None:
    with SASession(engine) as session:
        session.add(Tenant(id=tenant_id, name=tenant_id, sampling_budget_daily=100.0))
        session.add(BrandRow(id="b1", tenant_id=tenant_id, name="Acme", domain="acme.com"))
        session.add(
            Opportunity(
                id="o1",
                tenant_id=tenant_id,
                brand_id="b1",
                title="You're largely absent on gemini",
                rationale="mentioned in only 5% of gemini answers",
                engine="gemini",
                est_impact=0.9,
                source_gap="absence",
                status="open",
            )
        )
        session.commit()


def _real_opp_service_provider(
    session: SASession = Depends(deps.get_db_session),  # noqa: B008 (FastAPI Depends default)
    principal: Principal = Depends(deps.get_current_principal),  # noqa: B008
) -> DbOpportunityService:
    content_service = ContentService(
        kb_factory=lambda bid: KnowledgeBase(
            brand_id=bid, store=_NullVectorStore(), embedder=_NullEmbedder()
        ),
        llm=_NullLLM(),
        corpus=_NullCorpus(),
        claim_extractor=_NullClaimExtractor(),
        voice_scorer=_NullVoiceScorer(),
        voice_profile={},
        connectors={"hosted": _NullConnector()},
        store=DbAssetStore(session=session, tenant_id=principal.tenant_id),
        id_fn=lambda: "spawned-c1",
    )
    return DbOpportunityService(
        session=session, tenant_id=principal.tenant_id, content_service=content_service
    )


def _wire_real(client: TestClient) -> None:
    client.app.dependency_overrides[opportunities.get_opportunity_service] = (
        _real_opp_service_provider
    )


def test_list_reads_persisted_opportunities(
    app_client: TestClient, engine: Engine, make_token: Callable[..., str]
) -> None:
    _seed_opportunity(engine)
    _wire_real(app_client)
    r = app_client.get("/brands/b1/opportunities", headers=_auth(make_token(tenant_id="t1")))
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0] == {
        "id": "o1",
        "title": "You're largely absent on gemini",
        "rationale": "mentioned in only 5% of gemini answers",
        "est_impact": 0.9,
        "engine": "gemini",
    }


def test_act_spawns_and_persists_a_real_draft(
    app_client: TestClient, engine: Engine, make_token: Callable[..., str]
) -> None:
    _seed_opportunity(engine)
    _wire_real(app_client)
    r = app_client.post(
        "/opportunities/o1/act", json={}, headers=_auth(make_token(tenant_id="t1", role="editor"))
    )
    assert r.status_code == 200
    assert r.json() == {"content_id": "spawned-c1"}

    with SASession(engine) as session:
        asset = session.get(ContentAsset, "spawned-c1")
        opp = session.get(Opportunity, "o1")
    assert asset is not None  # the draft was persisted through the content pipeline
    assert asset.brand_id == "b1"
    assert asset.tenant_id == "t1"
    assert asset.status == "draft"  # still needs the human approval gate before publish
    assert opp is not None and opp.status == "acted"  # opportunity dropped out of the open queue


def test_act_unknown_opportunity_is_404(
    app_client: TestClient, engine: Engine, make_token: Callable[..., str]
) -> None:
    _seed_opportunity(engine)
    _wire_real(app_client)
    r = app_client.post(
        "/opportunities/nope/act",
        json={},
        headers=_auth(make_token(tenant_id="t1", role="editor")),
    )
    assert r.status_code == 404


def test_act_cross_tenant_opportunity_is_404(
    app_client: TestClient, engine: Engine, make_token: Callable[..., str]
) -> None:
    # o1 is owned by t1; a t2 editor acting on it must 404 (never a cross-tenant spawn or a leak).
    _seed_opportunity(engine, tenant_id="t1")
    _wire_real(app_client)
    r = app_client.post(
        "/opportunities/o1/act",
        json={},
        headers=_auth(make_token(tenant_id="t2", role="editor")),
    )
    assert r.status_code == 404
