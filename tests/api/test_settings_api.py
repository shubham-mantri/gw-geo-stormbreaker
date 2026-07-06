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


def test_snippet_points_at_local_pixel_not_cdn(
    app_client: TestClient, admin_token: str, settings
) -> None:
    # W4: the install snippet must point at the LOCAL, self-hosted pixel + local collect origin,
    # never the old cdn.gwgeo.io placeholder.
    r = app_client.get(
        "/lead-capture/snippet?brand_id=b1",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    snip = r.json()["snippet"]
    assert "cdn.gwgeo.io" not in snip
    assert f'src="{settings.pixel_url}"' in snip  # local /pixel/gwgeo.js
    assert f'data-api="{settings.pixel_api_base}"' in snip  # local collect origin


def test_snippet_requires_editor(app_client: TestClient, viewer_token: str) -> None:
    # review fix #4: a write-key is a credential, so minting one requires role >= editor (a viewer
    # -> 403), consistent with the sibling write endpoints.
    r = app_client.get(
        "/lead-capture/snippet?brand_id=b1",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert r.status_code == 403


def test_unknown_integration_kind_422(app_client: TestClient, admin_token: str) -> None:
    r = app_client.post(
        "/integrations/bogus",
        json={"config": {}},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code in (404, 422)
