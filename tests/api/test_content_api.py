"""Tests for the `/content` endpoints (M3-T22, ui-spec.md §3.5/§5/§6).

Reconciled to M2's API: the content router mounts into M2's ``create_app`` and reuses M2's
``get_current_principal``/``require_role``. ``ContentService`` is injected via the overridable
``content.get_content_service`` dependency (default raises), so these tests stub it with
``app.dependency_overrides[content.get_content_service]`` -- mirroring how ``create_app`` overrides
``leadcapture.get_db_session``. No live LLM/HTTP/DB call.

Fixtures (``app_client``, ``make_token``) live in ``tests/api/conftest.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from gw_geo.api.routers import content
from gw_geo.common.models import ContentDraft, ContentStatus, GuardrailReport
from gw_geo.content.approval import ApprovalError
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
    """A stub `ContentService`: returns fixtures, never touches an LLM/guardrail/connector."""

    def __init__(self, *, publish_error: bool = False) -> None:
        self._publish_error = publish_error

    def generate(self, **kwargs: Any) -> tuple[ContentDraft, GuardrailReport]:
        return _draft(), _report()

    def get_asset(self, content_id: str) -> tuple[ContentDraft, GuardrailReport]:
        return _draft(content_id), _report()

    def approve(
        self, draft: ContentDraft, *, report: GuardrailReport, role: str
    ) -> ContentDraft:
        return draft.model_copy(update={"status": ContentStatus.APPROVED})

    async def publish(self, draft: ContentDraft, *, connector: str) -> PublishResult:
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
