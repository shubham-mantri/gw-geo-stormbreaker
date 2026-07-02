# M3-T10 — API scaffold + tenant/role auth

**Depends on:** M0 db, T03 · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** A lightweight **FastAPI** app that hosts M3's `/opportunities` and `/content` routers
(ui-spec §6), with a tenant/role dependency (`Principal` from the bearer token; server-enforced scope,
ui-spec §5). Services are **injected** into the app factory so routers are testable with FastAPI
`TestClient` + `dependency_overrides` (no DB/LLM/HTTP live calls). Routers themselves land in T21/T22;
this task builds the app + auth seam + a health route.

**Files:**
- Create: `src/gw_geo/api/__init__.py`, `src/gw_geo/api/app.py`, `src/gw_geo/api/deps.py`
- Test: `tests/api/test_app.py`, `tests/api/__init__.py`

## Interface

```python
# deps.py
from pydantic import BaseModel
class Principal(BaseModel):
    tenant_id: str
    user_id: str
    role: str                              # owner|admin|editor|viewer

def get_principal() -> Principal: ...      # decodes bearer token; overridden in tests
def require_role(*roles: str): ...         # FastAPI dependency factory; 403 if role not allowed

# app.py
from dataclasses import dataclass
@dataclass
class Services:                            # injected service handles (filled by T20/T21/T22)
    opportunities: object | None = None
    content: object | None = None

def create_app(services: Services) -> "FastAPI": ...   # mounts routers, /healthz, auth dep
```

## Steps
- [ ] **1. Failing test** `tests/api/test_app.py`:

```python
from fastapi.testclient import TestClient
from gw_geo.api.app import create_app, Services
from gw_geo.api.deps import Principal, get_principal, require_role

def _client(role="editor"):
    app = create_app(Services())
    app.dependency_overrides[get_principal] = lambda: Principal(
        tenant_id="t1", user_id="u1", role=role)
    return TestClient(app), app

def test_healthz_ok():
    client, _ = _client()
    assert client.get("/healthz").json() == {"status": "ok"}

def test_require_role_allows_and_forbids():
    from fastapi import Depends, FastAPI
    app = create_app(Services())
    @app.get("/admin-only", dependencies=[Depends(require_role("admin", "owner"))])
    def _admin(): return {"ok": True}
    app.dependency_overrides[get_principal] = lambda: Principal(
        tenant_id="t1", user_id="u1", role="viewer")
    c = TestClient(app)
    assert c.get("/admin-only").status_code == 403
    app.dependency_overrides[get_principal] = lambda: Principal(
        tenant_id="t1", user_id="u1", role="admin")
    assert c.get("/admin-only").status_code == 200
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `deps.py` (`get_principal` raises 401 without a valid bearer in prod; trivially
  overridable in tests) and `app.py` (`create_app` returns a `FastAPI`, mounts `/healthz`, stores
  `services` on `app.state`). `require_role` reads `get_principal` and raises `HTTPException(403)`.
- [ ] **4. Run → pass**; mypy clean on new files.
- [ ] **5. Commit:** `feat(api): FastAPI app scaffold + tenant/role auth dependency`

## Acceptance
- `create_app(Services())` returns a working app with `/healthz`; `require_role` enforces RBAC (403 on
  disallowed role); `get_principal` is override-injectable for hermetic tests.
