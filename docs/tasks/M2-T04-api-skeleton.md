# M2-T04 — API skeleton + tenancy/RBAC dependencies

**Depends on:** T03 · **Wave:** 1 (after T03) · **Suggested agent:** general-purpose

**Goal:** The FastAPI app factory and the shared request dependencies every router uses:
`get_current_principal` (from the bearer JWT), `scoped_session` (builds a `TenantScopedSession` from
the token's `tenant_id` — TRD §7), and `require_role(...)` for write routes. Plus login/refresh
routes and uniform error handling. **No client-supplied tenant ever.**

**Files:**
- Create: `src/gw_geo/api/app.py`, `src/gw_geo/api/deps.py`, `src/gw_geo/api/schemas.py`,
  `src/gw_geo/handlers/api.py` (Mangum entrypoint)
- Test: `tests/api/test_app.py`

## Interface

```python
# deps.py
def get_current_principal(authorization: str = Header(...)) -> Principal: ...      # 401 on bad token
def scoped_session(principal = Depends(get_current_principal)) -> TenantScopedSession: ...
def require_role(minimum: str):    # returns a dependency; 403 if principal.role < minimum
    ...

# app.py
def create_app(settings=None) -> FastAPI: ...   # mounts routers, CORS(settings.cors_allow_origins),
                                                # POST /auth/login, POST /auth/refresh, error handlers
# handlers/api.py
handler = Mangum(create_app())
```

`POST /auth/login {email,password} -> 200 {access_token,refresh_token,role,tenant_id}` / `401`.
Errors: `AuthError -> 401`, unknown brand for tenant `-> 404`, RBAC fail `-> 403`, validation `-> 422`.

## Steps
- [ ] **1. Failing test** `tests/api/test_app.py` (FastAPI `TestClient`, SQLite, seeded user):

```python
from fastapi.testclient import TestClient
from gw_geo.api.app import create_app

def test_health_open(app_client):            # app_client: TestClient over create_app w/ test settings
    assert app_client.get("/healthz").status_code == 200

def test_login_returns_token(app_client, seeded_user):   # seeded_user: u@x.com / pw in tenant t1
    r = app_client.post("/auth/login", json={"email": "u@x.com", "password": "pw"})
    assert r.status_code == 200 and r.json()["tenant_id"] == "t1"

def test_protected_route_requires_bearer(app_client):
    assert app_client.get("/brands").status_code == 401   # no token

def test_require_role_blocks_viewer(app_client, viewer_token):
    # a write route guarded by require_role("editor") rejects a viewer
    r = app_client.post("/brands", json={"name":"A","domain":"a.com"},
                        headers={"Authorization": f"Bearer {viewer_token}"})
    assert r.status_code == 403
```
(Provide `app_client`, `seeded_user`, `viewer_token` fixtures in `tests/api/conftest.py`; a temporary
`POST /brands` guarded by `require_role("editor")` may be stubbed here and superseded by T13.)

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `create_app`, deps, `/auth/login`+`/auth/refresh`, `/healthz`, CORS, and the
  exception handlers mapping `AuthError→401`, `PermissionError→403`, `LookupError→404`. Wire
  `Mangum` in `handlers/api.py`. `scoped_session` MUST derive tenant only from the token.
- [ ] **4. Run → pass**; `mypy src/gw_geo/common` clean.
- [ ] **5. Commit:** `feat(api): fastapi skeleton + tenancy/rbac deps + auth routes`

## Acceptance
- `create_app()` returns a mountable FastAPI app; `/auth/login` issues tokens; missing/invalid bearer
  → 401; `require_role` → 403 for insufficient role; `scoped_session` ignores any client tenant input
  and uses the token's; Mangum entrypoint importable; hermetic.
