"""FastAPI app factory (m2-design.md §3): the tenant-scoped REST skin over the measurement /
attribution core.

``create_app()`` mounts the auth + lead-capture + visibility/sources routers, wires the
tenancy/RBAC dependencies, configures CORS for ``web/``, and maps domain errors to HTTP status
codes. Building the app performs no I/O -- the DB engine is created lazily and opens no connection
until first use -- so ``handlers/api.py`` can build it at import time.

Error mapping (m2-design.md §3, ui-spec.md §5): ``AuthError -> 401``, ``PermissionError -> 403``,
``LookupError -> 404`` (e.g. an unknown brand for the tenant -- a 404, never a 403 tenant leak).
Request-validation stays FastAPI's default ``422``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SASession

from gw_geo.api import auth
from gw_geo.api.auth import AuthError, TokenPair
from gw_geo.api.deps import get_db_session, get_settings_dep, require_role, scoped_session
from gw_geo.api.routers import leadcapture, visibility
from gw_geo.api.schemas import BrandCreate, BrandOut, LoginRequest, RefreshRequest
from gw_geo.common.config import Settings, get_settings
from gw_geo.common.db import Brand, TenantScopedSession

_auth_router = APIRouter(tags=["auth"])


@_auth_router.post("/auth/login", response_model=TokenPair)
def login(
    body: LoginRequest,
    session: Annotated[SASession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> TokenPair:
    """Verify credentials and issue an access+refresh token pair (``AuthError -> 401``)."""
    return auth.authenticate(
        session,
        email=body.email,
        password=body.password,
        secret=settings.jwt_secret,
        settings=settings,
    )


@_auth_router.post("/auth/refresh", response_model=TokenPair)
def refresh(
    body: RefreshRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> TokenPair:
    """Exchange a valid refresh token for a fresh token pair (``AuthError -> 401``)."""
    principal = auth.decode_token(body.refresh_token, secret=settings.jwt_secret)
    return auth.issue_tokens(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        role=principal.role,
        secret=settings.jwt_secret,
        access_ttl_s=settings.jwt_access_ttl_s,
        refresh_ttl_s=settings.jwt_refresh_ttl_s,
    )


_core_router = APIRouter()


@_core_router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe -- open, no auth."""
    return {"status": "ok"}


# --- Temporary /brands endpoints -------------------------------------------------------------------
# A real tenancy + RBAC target for T04's tests; superseded by the full brands router in T13. Kept
# minimal but genuinely functional (scoped read + scoped write) so it exercises the linchpin deps.


@_core_router.get("/brands", response_model=list[BrandOut])
def list_brands(scoped: Annotated[TenantScopedSession, Depends(scoped_session)]) -> list[BrandOut]:
    """List the authed tenant's brands (bearer required; missing/invalid -> 401)."""
    return [
        BrandOut(id=b.id, name=b.name, domain=b.domain, competitors=list(b.competitors))
        for b in scoped.query_brands()
    ]


@_core_router.post("/brands", status_code=201, dependencies=[Depends(require_role("editor"))])
def create_brand(
    body: BrandCreate,
    scoped: Annotated[TenantScopedSession, Depends(scoped_session)],
) -> dict[str, str]:
    """Create a brand for the authed tenant; requires >= editor (viewer -> 403)."""
    brand = Brand(
        id=uuid4().hex,
        tenant_id=scoped.tenant_id,
        name=body.name,
        domain=body.domain,
        competitors=[],
    )
    scoped.add(brand)
    scoped.commit()
    return {"id": brand.id}


def _install_exception_handlers(app: FastAPI) -> None:
    """Map domain exceptions to HTTP status codes (subclasses match via the exception MRO)."""

    def on_auth_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(exc) or "unauthorized"})

    def on_permission_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc) or "forbidden"})

    def on_lookup_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc) or "not found"})

    app.add_exception_handler(AuthError, on_auth_error)
    app.add_exception_handler(PermissionError, on_permission_error)
    app.add_exception_handler(LookupError, on_lookup_error)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the tenant-scoped FastAPI app: routers, CORS, tenancy/RBAC deps, and error handlers.

    ``settings`` defaults to :func:`gw_geo.common.config.get_settings` (env-driven). The chosen
    settings and a lazily-connecting DB engine are stashed on ``app.state`` so the request
    dependencies (``deps.py``) can read them without any global state.
    """
    settings = settings if settings is not None else get_settings()

    app = FastAPI(title="gw-geo API", version="0.0.1")
    app.state.settings = settings
    app.state.db_engine = create_engine(settings.database_url)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allow_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _install_exception_handlers(app)

    app.include_router(_auth_router)
    app.include_router(_core_router)
    app.include_router(leadcapture.router)
    app.include_router(visibility.router)

    # The public leadcapture router ships a deliberately-unimplemented get_db_session; point it at
    # the real (unscoped) session provider so the beacon can write. Its per-brand write-key -- not a
    # JWT -- authorizes it, so it never uses the tenant-scoped deps.
    app.dependency_overrides[leadcapture.get_db_session] = get_db_session

    return app
