"""M2-T04 spec tests for the FastAPI skeleton: health, login, bearer gating, RBAC, error mapping.

Fixtures (``app_client``, ``seeded_user``, ``make_token``, ``viewer_token``) live in
``tests/api/conftest.py`` and are shared with the T13-T16 router tasks.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from gw_geo.api import auth
from gw_geo.common.config import Settings


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


def test_refresh_issues_new_tokens(app_client: TestClient, settings: Settings) -> None:
    # /auth/refresh takes a *refresh* token (review fix #2): mint one rather than an access token.
    refresh = auth.issue_tokens(
        user_id="u1", tenant_id="t1", role="editor", secret=settings.jwt_secret
    ).refresh_token
    r = app_client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "t1" and body["role"] == "editor"
    assert body["access_token"] and body["refresh_token"]


def test_access_token_rejected_at_refresh(
    app_client: TestClient, make_token: Callable[..., str]
) -> None:
    # review fix #2: /auth/refresh requires a *refresh* token; an access token must be rejected.
    access = make_token(role="editor", tenant_id="t1")  # make_token mints access tokens
    r = app_client.post("/auth/refresh", json={"refresh_token": access})
    assert r.status_code == 401


def test_refresh_token_rejected_on_data_route(
    app_client: TestClient, settings: Settings
) -> None:
    # review fix #2: a refresh token must not authorize a data route (access-token-only).
    refresh = auth.issue_tokens(
        user_id="u1", tenant_id="t1", role="editor", secret=settings.jwt_secret
    ).refresh_token
    r = app_client.get("/brands", headers={"Authorization": f"Bearer {refresh}"})
    assert r.status_code == 401


def test_malformed_role_denied_not_500(
    app_client: TestClient, make_token: Callable[..., str]
) -> None:
    # review fix #5: an unknown role hitting an RBAC-gated route -> 403 (deny), never a 500.
    token = make_token(role="superuser", tenant_id="t1")  # not in ROLES
    r = app_client.post(
        "/brands", json={"name": "A", "domain": "a.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


def test_public_beacon_cors_open_for_any_origin(app_client: TestClient) -> None:
    # review fix #6: the public pixel beacon accepts cross-origin POSTs (allow-origin: *, no creds).
    r = app_client.options(
        "/lead-capture/collect",
        headers={
            "Origin": "https://a-customer-site.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "*"
    # public + write-key-authorized: credentials must NOT be allowed on this open policy.
    assert r.headers.get("access-control-allow-credentials") != "true"


def test_authed_api_cors_not_loosened(app_client: TestClient) -> None:
    # review fix #6: the beacon's permissive CORS must NOT bleed onto the authed API -- a foreign
    # origin never gets a wildcard allow-origin on a non-beacon route.
    r = app_client.get(
        "/healthz", headers={"Origin": "https://a-customer-site.example"}
    )
    assert r.headers.get("access-control-allow-origin") != "*"


def test_mangum_handler_importable() -> None:
    # Acceptance: the Lambda entrypoint imports (building the app opens no DB connection).
    from gw_geo.handlers.api import handler

    assert handler is not None
