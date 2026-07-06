"""Tests for the settings-screen endpoints (M2-T16, ui-spec.md §6/§3.8).

Fixtures (``app_client``, ``t1_token``, ``editor_token``, ``admin_token``) live in
``tests/api/conftest.py``. Hermetic: in-memory SQLite, no live calls.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient


def test_prompts_crud_scoped(
    app_client: TestClient, t1_token: str, seeded_brands: None
) -> None:
    # seeded_brands seeds Tenant t1 + Brand b1 so the created Prompt's tenant_id/brand_id FKs
    # resolve (FK enforcement is on in the suite; without the parents the POST would 500).
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


# --- LLM model config (M5 model-selection) -----------------------------------------------------


def test_llm_model_get_empty_before_any_write(app_client: TestClient, admin_token: str) -> None:
    # Hermetic DB is `create_all`'d (not migration-seeded), so the table starts empty.
    r = app_client.get(
        "/settings/llm-model", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    assert r.json() == []


def test_llm_model_put_then_get_roundtrip(app_client: TestClient, admin_token: str) -> None:
    h = {"Authorization": f"Bearer {admin_token}"}
    p1 = app_client.put(
        "/settings/llm-model", json={"gateway": "local_claude", "chat_model": "opus"}, headers=h
    )
    assert p1.status_code == 200
    assert p1.json() == {"gateway": "local_claude", "chat_model": "opus"}
    app_client.put(
        "/settings/llm-model",
        json={"gateway": "portkey", "chat_model": "claude-opus-4-8"},
        headers=h,
    )
    rows = app_client.get("/settings/llm-model", headers=h).json()
    # Ordered by gateway; both upserts present.
    assert rows == [
        {"gateway": "local_claude", "chat_model": "opus"},
        {"gateway": "portkey", "chat_model": "claude-opus-4-8"},
    ]


def test_llm_model_put_is_idempotent_upsert(app_client: TestClient, admin_token: str) -> None:
    h = {"Authorization": f"Bearer {admin_token}"}
    app_client.put(
        "/settings/llm-model", json={"gateway": "portkey", "chat_model": "claude-sonnet-4-5"},
        headers=h,
    )
    # Second PUT for the same gateway updates in place (no duplicate row).
    app_client.put(
        "/settings/llm-model", json={"gateway": "portkey", "chat_model": "claude-opus-4-8"},
        headers=h,
    )
    rows = app_client.get("/settings/llm-model", headers=h).json()
    assert rows == [{"gateway": "portkey", "chat_model": "claude-opus-4-8"}]


def test_llm_model_owner_may_write(app_client: TestClient, make_token: Callable[..., str]) -> None:
    # owner outranks admin in ROLES, so it also passes require_role("admin").
    owner = make_token(role="owner")
    r = app_client.put(
        "/settings/llm-model",
        json={"gateway": "local_claude", "chat_model": "haiku"},
        headers={"Authorization": f"Bearer {owner}"},
    )
    assert r.status_code == 200


def test_llm_model_put_requires_admin(app_client: TestClient, editor_token: str) -> None:
    r = app_client.put(
        "/settings/llm-model",
        json={"gateway": "local_claude", "chat_model": "opus"},
        headers={"Authorization": f"Bearer {editor_token}"},
    )
    assert r.status_code == 403  # editor < admin


def test_llm_model_get_requires_admin(app_client: TestClient, viewer_token: str) -> None:
    r = app_client.get(
        "/settings/llm-model", headers={"Authorization": f"Bearer {viewer_token}"}
    )
    assert r.status_code == 403  # viewer < admin


def test_llm_model_requires_auth(app_client: TestClient) -> None:
    assert app_client.get("/settings/llm-model").status_code == 401
    assert (
        app_client.put(
            "/settings/llm-model", json={"gateway": "portkey", "chat_model": "x"}
        ).status_code
        == 401
    )
