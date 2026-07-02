"""Shared FastAPI request dependencies (m2-design.md §3/§7, TRD §7).

Every authed route resolves its :class:`~gw_geo.api.auth.Principal` from the bearer JWT and runs
through a :class:`~gw_geo.common.db.TenantScopedSession` built from *the token's* ``tenant_id`` --
never a client-supplied tenant, so cross-tenant access is impossible by construction.
``require_role`` gates write routes.

``Settings`` and the DB engine are read from ``app.state`` (populated by ``create_app``), so a test
can build the app with SQLite test settings and these dependencies transparently follow -- no global
mutation, no import-time I/O.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session as SASession

from gw_geo.api import auth
from gw_geo.api.auth import AuthError, Principal
from gw_geo.common.config import Settings
from gw_geo.common.db import TenantScopedSession


def get_settings_dep(request: Request) -> Settings:
    """The :class:`Settings` the app was created with (stored on ``app.state`` by ``create_app``)."""
    settings: Settings = request.app.state.settings
    return settings


def get_db_session(request: Request) -> Iterator[SASession]:
    """Yield a plain SQLAlchemy ``Session`` bound to the app's engine (from ``settings.database_url``).

    Deliberately *unscoped*: authed routes wrap it via :func:`scoped_session`, while the public
    leadcapture router needs it unscoped (it runs before any tenant is known). Closed after the
    request. ``create_app`` overrides the leadcapture router's own placeholder provider with this
    one.
    """
    session = SASession(request.app.state.db_engine)
    try:
        yield session
    finally:
        session.close()


def get_current_principal(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Resolve the caller's :class:`Principal` from the ``Authorization: Bearer <jwt>`` header.

    Raises :class:`AuthError` (mapped to **401**) when the header is missing/malformed or the token
    is invalid or expired. No DB lookup: the token itself carries ``tenant_id`` + ``role``.
    """
    if authorization is None:
        raise AuthError("missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthError("Authorization header must be 'Bearer <token>'")
    return auth.decode_token(token, secret=settings.jwt_secret)


def scoped_session(
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: Annotated[SASession, Depends(get_db_session)],
) -> TenantScopedSession:
    """A ``TenantScopedSession`` bound to the *token's* tenant (TRD §7) -- never client-supplied."""
    return TenantScopedSession(session, principal.tenant_id)


def require_role(minimum: str) -> Callable[..., Principal]:
    """Build a dependency admitting only principals with at least ``minimum`` privilege.

    Raises :class:`PermissionError` (mapped to **403**) when ``principal.role`` ranks below
    ``minimum`` in :data:`gw_geo.api.auth.ROLES`. Returns the :class:`Principal` on success, so a
    route can both gate on and read the caller in one dependency.
    """

    def _require_role(principal: Annotated[Principal, Depends(get_current_principal)]) -> Principal:
        if not auth.role_at_least(principal.role, minimum):
            raise PermissionError(f"role {principal.role!r} is below required role {minimum!r}")
        return principal

    return _require_role
