"""Backend auth core (m2-design.md §7): argon2 password hashing, JWT issue/verify, RBAC ordering.

Self-contained backend JWT -- no Clerk/Auth0. Pure functions plus a small `authenticate` service;
no web framework here (routes + dependencies land in T04). Both the access and refresh token carry
`tenant_id` + `role` alongside the user id, so a verified token resolves a full `Principal` without
a DB round-trip -- `api/deps.py` (T04) builds its `TenantScopedSession` straight from the decoded
token's `tenant_id`.

Importing this module has no side effects: no DB/network I/O, no settings loaded at import time.
"""

from __future__ import annotations

import time

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from pydantic import BaseModel
from sqlalchemy.orm import Session as SASession

from gw_geo.common.config import Settings
from gw_geo.common.db import AppUser, Membership

# Ascending privilege -- index into this tuple is the RBAC ordering `role_at_least` compares on.
ROLES: tuple[str, ...] = ("viewer", "editor", "admin", "owner")

_JWT_ALGORITHM = "HS256"

# Stateless (holds only hashing parameters); safe to share across calls, no I/O at construction.
_hasher = PasswordHasher()


class Principal(BaseModel):
    user_id: str
    tenant_id: str
    role: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    role: str
    tenant_id: str


class AuthError(Exception):
    """Raised for any auth failure: bad credentials, no membership, or a bad/expired token."""


def hash_password(pw: str) -> str:
    """Hash `pw` with argon2id (argon2-cffi's recommended defaults)."""
    return _hasher.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    """Return whether `pw` matches `hashed`.

    `False` on mismatch or a malformed `hashed` value -- never raises for bad input.
    """
    try:
        return _hasher.verify(hashed, pw)
    except (VerificationError, InvalidHashError):
        return False


def _encode(*, user_id: str, tenant_id: str, role: str, secret: str, ttl_s: int) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "iat": now,
        "exp": now + ttl_s,
    }
    return jwt.encode(payload, secret, algorithm=_JWT_ALGORITHM)


def issue_tokens(
    *,
    user_id: str,
    tenant_id: str,
    role: str,
    secret: str,
    access_ttl_s: int = 900,
    refresh_ttl_s: int = 1_209_600,
) -> TokenPair:
    """Issue an access+refresh JWT pair (HS256), both carrying `tenant_id` + `role`."""
    access_token = _encode(
        user_id=user_id, tenant_id=tenant_id, role=role, secret=secret, ttl_s=access_ttl_s
    )
    refresh_token = _encode(
        user_id=user_id, tenant_id=tenant_id, role=role, secret=secret, ttl_s=refresh_ttl_s
    )
    return TokenPair(
        access_token=access_token, refresh_token=refresh_token, role=role, tenant_id=tenant_id
    )


def decode_token(token: str, *, secret: str) -> Principal:
    """Decode and verify `token`, returning its `Principal`.

    Raises:
        AuthError: `token` is malformed, tampered with, expired, or missing a required claim.
    """
    try:
        payload = jwt.decode(token, secret, algorithms=[_JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AuthError("invalid or expired token") from exc
    try:
        return Principal(
            user_id=payload["sub"], tenant_id=payload["tenant_id"], role=payload["role"]
        )
    except KeyError as exc:
        raise AuthError("token missing required claim") from exc


def role_at_least(role: str, minimum: str) -> bool:
    """Whether `role` has at least `minimum`'s privilege, per the `ROLES` ordering."""
    return ROLES.index(role) >= ROLES.index(minimum)


def authenticate(
    session: SASession, *, email: str, password: str, secret: str, settings: Settings
) -> TokenPair:
    """Verify an `AppUser`'s password and resolve their `Membership`, then issue tokens.

    Looks up `AppUser` by `email`, verifies `password` against its stored hash, then reads the
    user's `Membership` for the `tenant_id` + `role` to embed in the issued tokens. `session` is a
    plain (not tenant-scoped) session: at login time the caller's tenant is not yet known -- that's
    exactly what this function resolves.

    Raises:
        AuthError: no `AppUser` with `email`, the password doesn't match, or the user has no
            `Membership` (nothing to scope a token to).
    """
    user = session.query(AppUser).filter(AppUser.email == email).first()
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("invalid email or password")

    membership = session.query(Membership).filter(Membership.user_id == user.id).first()
    if membership is None:
        raise AuthError("user has no tenant membership")

    return issue_tokens(
        user_id=user.id,
        tenant_id=membership.tenant_id,
        role=membership.role,
        secret=secret,
        access_ttl_s=settings.jwt_access_ttl_s,
        refresh_ttl_s=settings.jwt_refresh_ttl_s,
    )
