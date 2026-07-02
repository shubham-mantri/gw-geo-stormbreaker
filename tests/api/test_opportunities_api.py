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

from fastapi.testclient import TestClient

from gw_geo.api.routers import opportunities


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
