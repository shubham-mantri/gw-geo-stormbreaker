"""FastAPI app factory (m2-design.md §3): the tenant-scoped REST skin over the measurement /
attribution core.

``create_app()`` mounts the auth + lead-capture + brands/overview + visibility/sources +
settings (prompts/integrations/snippet) routers, wires the tenancy/RBAC dependencies, configures
CORS for ``web/``, and maps domain errors to HTTP status codes. Building the app performs no I/O --
the DB engine is created lazily and opens no connection until first use -- so ``handlers/api.py``
can build it at import time.

Error mapping (m2-design.md §3, ui-spec.md §5): ``AuthError -> 401``, ``PermissionError -> 403``,
``LookupError -> 404`` (e.g. an unknown brand for the tenant -- a 404, never a 403 tenant leak).
Request-validation stays FastAPI's default ``422``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SASession
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from gw_geo.api import auth
from gw_geo.api.auth import AuthError, TokenPair
from gw_geo.api.deps import get_db_session, get_settings_dep
from gw_geo.api.routers import brands, leadcapture, pipeline, visibility
from gw_geo.api.routers import settings as settings_router
from gw_geo.api.schemas import LoginRequest, RefreshRequest
from gw_geo.common.config import Settings, get_settings

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
    """Exchange a valid refresh token for a fresh token pair (``AuthError -> 401``).

    Requires a *refresh* token specifically (``expected_type="refresh"``): an access token replayed
    here is rejected, so a short-lived access token can never be laundered into a fresh pair.
    """
    principal = auth.decode_token(
        body.refresh_token, secret=settings.jwt_secret, expected_type="refresh"
    )
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


# The one public, unauthenticated endpoint (the pixel beacon). It is embedded on arbitrary
# third-party customer sites, so it needs its own permissive CORS -- separate from the credentialed,
# dashboard-only policy that guards the authed API.
_PUBLIC_BEACON_PATH = "/lead-capture/collect"


async def _public_beacon_cors(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Scoped, permissive CORS for ONLY ``POST /lead-capture/collect`` (m2-design §6).

    The app-wide :class:`CORSMiddleware` is credentialed and locked to the dashboard origin, which
    is correct for the authed API but would block the pixel -- served from any customer domain --
    from beaconing here cross-origin. This middleware opens just that one route: ``allow-origin: *``,
    ``POST``/``OPTIONS`` only, and **no** credentials (the write-key in the body authorizes the
    write; cookies/Authorization are never used on this endpoint, so ``*`` is safe). Every other
    path is passed straight through to the credentialed policy untouched.

    Registered last in :func:`create_app` so it sits *outside* the app-wide policy and can answer
    the cross-origin preflight that policy would otherwise reject. Per-brand Origin allowlisting
    (keyed on the write-key's brand) is the M3 hardening.
    """
    if request.url.path != _PUBLIC_BEACON_PATH:
        return await call_next(request)
    if request.method == "OPTIONS":
        response: Response = Response(status_code=200)  # short-circuit the preflight
    else:
        response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    requested = request.headers.get("access-control-request-headers")
    response.headers["Access-Control-Allow-Headers"] = requested or "*"
    return response


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
    # Added AFTER the app-wide policy so it is the OUTERMOST middleware (Starlette applies the
    # last-added first): it can intercept the public beacon's cross-origin preflight before the
    # credentialed policy above rejects it, while leaving every authed route on that strict policy.
    app.add_middleware(BaseHTTPMiddleware, dispatch=_public_beacon_cors)

    _install_exception_handlers(app)

    app.include_router(_auth_router)
    app.include_router(_core_router)
    app.include_router(brands.router)
    app.include_router(leadcapture.router)
    app.include_router(pipeline.router)
    app.include_router(visibility.router)
    # Imported as `settings_router` (not `settings`): this function's own `settings` parameter
    # would otherwise shadow the module import for the rest of this function body.
    app.include_router(settings_router.router)

    # The public leadcapture router ships a deliberately-unimplemented get_db_session; point it at
    # the real (unscoped) session provider so the beacon can write. Its per-brand write-key -- not a
    # JWT -- authorizes it, so it never uses the tenant-scoped deps.
    app.dependency_overrides[leadcapture.get_db_session] = get_db_session

    return app
