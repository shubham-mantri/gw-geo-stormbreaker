"""M2-T04 spec tests for the FastAPI skeleton: health, login, bearer gating, RBAC, error mapping.

Fixtures (``app_client``, ``seeded_user``, ``make_token``, ``viewer_token``) live in
``tests/api/conftest.py`` and are shared with the T13-T16 router tasks.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient


def test_health_open(app_client: TestClient) -> None:
    assert app_client.get("/healthz").status_code == 200


def test_login_returns_token(app_client: TestClient, seeded_user: dict[str, str]) -> None:
    r = app_client.post("/auth/login", json={"email": "u@x.com", "password": "pw"})
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "t1"
    assert body["access_token"] and body["refresh_token"]


def test_protected_route_requires_bearer(app_client: TestClient) -> None:
    assert app_client.get("/brands").status_code == 401


def test_require_role_blocks_viewer(app_client: TestClient, viewer_token: str) -> None:
    r = app_client.post(
        "/brands",
        json={"name": "A", "domain": "a.com"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert r.status_code == 403


# --- additional coverage: error handlers, invalid tokens, RBAC allow-path, tenancy, refresh ---


def test_login_wrong_password_returns_401(
    app_client: TestClient, seeded_user: dict[str, str]
) -> None:
    r = app_client.post("/auth/login", json={"email": "u@x.com", "password": "nope"})
    assert r.status_code == 401


def test_invalid_bearer_returns_401(app_client: TestClient) -> None:
    r = app_client.get("/brands", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_editor_can_create_and_list_brands(
    app_client: TestClient, seeded_user: dict[str, str], make_token: Callable[..., str]
) -> None:
    headers = {"Authorization": f"Bearer {make_token(role='editor', tenant_id='t1')}"}
    created = app_client.post(
        "/brands", json={"name": "Acme", "domain": "acme.com"}, headers=headers
    )
    assert created.status_code == 201
    brand_id = created.json()["id"]

    listed = app_client.get("/brands", headers=headers)
    assert listed.status_code == 200
    assert any(b["id"] == brand_id for b in listed.json())


def test_scoped_session_ignores_other_tenant(
    app_client: TestClient, seeded_user: dict[str, str], make_token: Callable[..., str]
) -> None:
    # A brand created by a t1 editor is invisible to a t2 principal: scope comes from the token.
    t1 = {"Authorization": f"Bearer {make_token(role='editor', tenant_id='t1')}"}
    app_client.post("/brands", json={"name": "Acme", "domain": "acme.com"}, headers=t1)

    t2 = {"Authorization": f"Bearer {make_token(role='editor', tenant_id='t2')}"}
    listed = app_client.get("/brands", headers=t2)
    assert listed.status_code == 200
    assert listed.json() == []


def test_refresh_issues_new_tokens(app_client: TestClient, make_token: Callable[..., str]) -> None:
    token = make_token(role="editor", tenant_id="t1")
    r = app_client.post("/auth/refresh", json={"refresh_token": token})
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "t1" and body["role"] == "editor"
    assert body["access_token"] and body["refresh_token"]


def test_mangum_handler_importable() -> None:
    # Acceptance: the Lambda entrypoint imports (building the app opens no DB connection).
    from gw_geo.handlers.api import handler

    assert handler is not None
