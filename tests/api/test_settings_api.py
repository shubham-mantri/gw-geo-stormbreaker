"""Tests for the settings-screen endpoints (M2-T16, ui-spec.md §6/§3.8).

Fixtures (``app_client``, ``t1_token``, ``editor_token``, ``admin_token``) live in
``tests/api/conftest.py``. Hermetic: in-memory SQLite, no live calls.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_prompts_crud_scoped(app_client: TestClient, t1_token: str) -> None:
    c = app_client.post(
        "/brands/b1/prompts",
        json={"text": "best CRM for startups", "geo": "us"},
        headers={"Authorization": f"Bearer {t1_token}"},
    )
    assert c.status_code == 201
    r = app_client.get("/brands/b1/prompts", headers={"Authorization": f"Bearer {t1_token}"})
    assert any(p["text"] == "best CRM for startups" for p in r.json())


def test_integration_connect_requires_admin(app_client: TestClient, editor_token: str) -> None:
    r = app_client.post(
        "/integrations/hubspot",
        json={"config": {"access_token_ref": "ssm://x"}},
        headers={"Authorization": f"Bearer {editor_token}"},
    )
    assert r.status_code == 403  # editor < admin


def test_snippet_contains_writekey(app_client: TestClient, admin_token: str) -> None:
    r = app_client.get(
        "/lead-capture/snippet?brand_id=b1",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    snip = r.json()["snippet"]
    assert "gwgeo.js" in snip and "data-key=" in snip


def test_unknown_integration_kind_422(app_client: TestClient, admin_token: str) -> None:
    r = app_client.post(
        "/integrations/bogus",
        json={"config": {}},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code in (404, 422)
