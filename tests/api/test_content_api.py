"""Tests for the `/content` endpoints (M3-T22, ui-spec.md §3.5/§5/§6).

Reconciled to M2's API: the content router mounts into M2's ``create_app`` and reuses M2's
``get_current_principal``/``require_role``. ``ContentService`` is injected via the overridable
``content.get_content_service`` dependency (default raises), so these tests stub it with
``app.dependency_overrides[content.get_content_service]`` -- mirroring how ``create_app`` overrides
``leadcapture.get_db_session``. No live LLM/HTTP/DB call.

Fixtures (``app_client``, ``make_token``) live in ``tests/api/conftest.py``.

``test_cross_tenant_approve_and_publish_are_404`` wires the **real** ``ContentService`` (in-memory
fakes for its LLM/KB/guardrail/connector collaborators, same hermetic style as
``tests/content/test_pipeline.py``) rather than ``StubContent``, because the tenant-scoping fix it
verifies lives inside ``ContentService`` itself -- ``StubContent`` always returns its fixture
regardless of tenant, so it cannot exercise the fix.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from gw_geo.api.routers import content
from gw_geo.common.models import ContentDraft, ContentStatus, GuardrailReport
from gw_geo.content.approval import ApprovalError
from gw_geo.content.kb import KnowledgeBase
from gw_geo.content.pipeline import ContentService
from gw_geo.content.publish.base import PublishResult


def _draft(content_id: str = "c1") -> ContentDraft:
    return ContentDraft(
        id=content_id, tenant_id="t1", brand_id="b1", title="Best CRM", body_markdown="x"
    )


def _report() -> GuardrailReport:
    return GuardrailReport(
        originality_ok=True,
        originality_score=0.1,
        claims_ok=True,
        unverified_claims=[],
        brand_voice_ok=True,
        brand_voice_score=0.9,
        passed=True,
    )


class StubContent:
    """A stub `ContentService`: returns fixtures, never touches an LLM/guardrail/connector.

    Mirrors the real `ContentService`'s tenant-aware signature (`get_asset`/`approve`/`publish`
    all take `tenant_id`) even though this stub doesn't itself enforce tenant scoping -- the
    real enforcement is covered against the actual `ContentService` in
    `test_cross_tenant_approve_and_publish_404` below and in `tests/content/test_pipeline.py`.
    """

    def __init__(self, *, publish_error: bool = False) -> None:
        self._publish_error = publish_error

    def generate(self, **kwargs: Any) -> tuple[ContentDraft, GuardrailReport]:
        return _draft(), _report()

    def get_asset(
        self, *, tenant_id: str, content_id: str
    ) -> tuple[ContentDraft, GuardrailReport]:
        return _draft(content_id), _report()

    def approve(
        self, draft: ContentDraft, *, report: GuardrailReport, role: str, tenant_id: str
    ) -> ContentDraft:
        return draft.model_copy(update={"status": ContentStatus.APPROVED})

    async def publish(
        self, draft: ContentDraft, *, connector: str, tenant_id: str
    ) -> PublishResult:
        if self._publish_error:
            raise ApprovalError("cannot publish from status 'draft' (must be APPROVED)")
        return PublishResult(
            published_url=f"https://kb.example.com/{draft.brand_id}/{draft.id}",
            external_id="ext-1",
            connector=connector,
        )


def _wire(client: TestClient, stub: StubContent) -> TestClient:
    """Point the app's `get_content_service` at `stub` (the injected-service test seam)."""
    client.app.dependency_overrides[content.get_content_service] = lambda: stub
    return client


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_generate_returns_uispec_shape(
    app_client: TestClient, make_token: Callable[..., str]
) -> None:
    client = _wire(app_client, StubContent())
    r = client.post(
        "/content/generate",
        json={"brand_id": "b1", "prompt_text": "best crm"},
        headers=_auth(make_token(role="editor")),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["content_id"] == "c1"
    assert set(body["guardrails"]) == {"claims_ok", "originality_ok"}  # ui-spec §6 exactly
    assert body["guardrails"]["claims_ok"] is True
    assert body["guardrails"]["originality_ok"] is True
    assert body["draft"]["id"] == "c1"  # the editable draft is returned too


def test_generate_requires_auth(app_client: TestClient) -> None:
    client = _wire(app_client, StubContent())
    r = client.post("/content/generate", json={"brand_id": "b1", "prompt_text": "x"})
    assert r.status_code == 401  # no bearer token


def test_viewer_cannot_approve(
    app_client: TestClient, make_token: Callable[..., str]
) -> None:
    client = _wire(app_client, StubContent())
    r = client.post("/content/c1/approve", json={}, headers=_auth(make_token(role="viewer")))
    assert r.status_code == 403  # RBAC gate (ui-spec §5): viewer cannot approve


def test_viewer_cannot_publish(
    app_client: TestClient, make_token: Callable[..., str]
) -> None:
    client = _wire(app_client, StubContent())
    r = client.post("/content/c1/publish", json={}, headers=_auth(make_token(role="viewer")))
    assert r.status_code == 403  # RBAC gate: viewer cannot publish


def test_editor_can_approve(
    app_client: TestClient, make_token: Callable[..., str]
) -> None:
    client = _wire(app_client, StubContent())
    r = client.post("/content/c1/approve", json={}, headers=_auth(make_token(role="editor")))
    assert r.status_code == 200
    assert r.json() == {"status": "approved"}  # ui-spec §6: {status}


def test_publish_returns_status_and_url(
    app_client: TestClient, make_token: Callable[..., str]
) -> None:
    client = _wire(app_client, StubContent())
    r = client.post(
        "/content/c1/publish",
        json={"connector": "hosted"},
        headers=_auth(make_token(role="admin")),
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"status", "published_url"}  # ui-spec §6: {status, published_url}
    assert body["status"] == "published"
    assert body["published_url"] == "https://kb.example.com/b1/c1"


def test_publish_unapproved_is_blocked_at_api(
    app_client: TestClient, make_token: Callable[..., str]
) -> None:
    # The approval gate is enforced at the API boundary: an authorized role publishing a draft the
    # service refuses (ApprovalError) gets a 409, never a publish. The honesty gate holds even for
    # an editor/admin -- RBAC alone is not the gate.
    client = _wire(app_client, StubContent(publish_error=True))
    r = client.post("/content/c1/publish", json={}, headers=_auth(make_token(role="editor")))
    assert r.status_code == 409


# --------------------------------------------------------------------------------------------
# Cross-tenant IDOR regression (M3 review): a tenant-B token must never resolve tenant-A's
# content id. In-memory fakes for the REAL `ContentService`'s collaborators -- none of these
# score/verify anything meaningfully, they only need to make `generate`/`run_guardrails` complete
# without a live call, since this test's assertions are about tenant scoping, not guardrail
# outcomes.
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

    async def publish(self, draft: ContentDraft, *, freshness: dict[str, Any]) -> PublishResult:
        return PublishResult(
            published_url=f"https://kb.example.com/{draft.brand_id}/{draft.id}",
            external_id=f"ext-{draft.id}",
            connector=self.name,
        )


def _real_content_service() -> ContentService:
    """A real `ContentService` wired from null/no-op fakes -- deterministic id, always-passing
    guardrails (so the legitimate same-tenant path in the test below also proves the fix doesn't
    just break the endpoint outright)."""
    return ContentService(
        kb=KnowledgeBase(brand_id="b1", store=_NullVectorStore(), embedder=_NullEmbedder()),
        llm=_NullLLM(),
        corpus=_NullCorpus(),
        claim_extractor=_NullClaimExtractor(),
        voice_scorer=_NullVoiceScorer(),
        voice_profile={},
        connectors={"hosted": _NullConnector()},
        id_fn=lambda: "tenant-a-draft",
    )


def test_cross_tenant_approve_and_publish_are_404(
    app_client: TestClient, make_token: Callable[..., str]
) -> None:
    """A tenant-B token calling `/content/{tenant-A-id}/approve` or `/publish` gets 404 -- never
    200 (an actual cross-tenant approve/publish) or 403 (which would confirm the id exists under
    another tenant). Regression for the id-addressed content IDOR (M3 review finding #1).
    """
    svc = _real_content_service()
    client = app_client
    client.app.dependency_overrides[content.get_content_service] = lambda: svc

    gen = client.post(
        "/content/generate",
        json={"brand_id": "b1", "prompt_text": "best crm"},
        headers=_auth(make_token(tenant_id="tenant-a", role="editor")),
    )
    assert gen.status_code == 200
    content_id = gen.json()["content_id"]
    assert content_id == "tenant-a-draft"

    tenant_b = _auth(make_token(tenant_id="tenant-b", role="editor"))
    approve_resp = client.post(f"/content/{content_id}/approve", json={}, headers=tenant_b)
    assert approve_resp.status_code == 404
    publish_resp = client.post(
        f"/content/{content_id}/publish", json={"connector": "hosted"}, headers=tenant_b
    )
    assert publish_resp.status_code == 404

    # Sanity: the owning tenant can still approve + publish its own draft -- the fix scopes
    # resolution by tenant, it doesn't just break the endpoint for everyone.
    tenant_a = _auth(make_token(tenant_id="tenant-a", role="editor"))
    own_approve = client.post(f"/content/{content_id}/approve", json={}, headers=tenant_a)
    assert own_approve.status_code == 200
    own_publish = client.post(
        f"/content/{content_id}/publish", json={"connector": "hosted"}, headers=tenant_a
    )
    assert own_publish.status_code == 200
