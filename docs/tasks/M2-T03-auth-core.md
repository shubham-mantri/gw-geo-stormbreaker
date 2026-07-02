# M2-T03 — Auth core (JWT + argon2 + RBAC)

**Depends on:** T02 · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** Self-contained backend auth (m2-design §1/§7): argon2 password hashing, JWT access/refresh
issue+verify carrying `tenant_id` + `role`, and an RBAC role ordering. No Clerk/Auth0. Pure
functions + a small service; no web framework here (routes are T04).

**Files:**
- Create: `src/gw_geo/api/__init__.py`, `src/gw_geo/api/auth.py`
- Test: `tests/api/test_auth.py`

## Interface

```python
from pydantic import BaseModel

ROLES = ("viewer", "editor", "admin", "owner")   # ascending privilege

class Principal(BaseModel):
    user_id: str; tenant_id: str; role: str

class TokenPair(BaseModel):
    access_token: str; refresh_token: str; role: str; tenant_id: str

def hash_password(pw: str) -> str: ...                     # argon2
def verify_password(pw: str, hashed: str) -> bool: ...
def issue_tokens(*, user_id: str, tenant_id: str, role: str, secret: str,
                 access_ttl_s: int = 900, refresh_ttl_s: int = 1209600) -> TokenPair: ...
def decode_token(token: str, *, secret: str) -> Principal: ...   # raises AuthError on bad/expired
def role_at_least(role: str, minimum: str) -> bool: ...    # RBAC ordering via ROLES index

class AuthError(Exception): ...

def authenticate(session, *, email: str, password: str, secret: str,
                 settings) -> TokenPair: ...  # verify user+membership, issue tokens; AuthError if bad
```

## Steps
- [ ] **1. Failing test** `tests/api/test_auth.py`:

```python
import pytest, time
from gw_geo.api import auth

def test_hash_roundtrip():
    h = auth.hash_password("hunter2")
    assert auth.verify_password("hunter2", h) and not auth.verify_password("x", h)

def test_token_roundtrip_carries_tenant_and_role():
    tp = auth.issue_tokens(user_id="u1", tenant_id="t1", role="editor", secret="k")
    p = auth.decode_token(tp.access_token, secret="k")
    assert p.user_id == "u1" and p.tenant_id == "t1" and p.role == "editor"

def test_expired_token_raises():
    tp = auth.issue_tokens(user_id="u1", tenant_id="t1", role="viewer", secret="k", access_ttl_s=-1)
    with pytest.raises(auth.AuthError):
        auth.decode_token(tp.access_token, secret="k")

def test_rbac_ordering():
    assert auth.role_at_least("admin", "editor")
    assert not auth.role_at_least("viewer", "editor")
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** with `argon2-cffi` (hashing) + `PyJWT` (HS256). `authenticate` looks up
  `AppUser` by email, verifies hash, reads the user's `Membership` (tenant_id+role), issues tokens.
  Keep import side-effect-free.
- [ ] **4. Run → pass**; add a test for `authenticate` with a seeded `AppUser`+`Membership` on SQLite.
- [ ] **5. Commit:** `feat(api): jwt+argon2 auth core with rbac`

## Acceptance
- Password hash/verify works; tokens carry `tenant_id`+`role` and reject tamper/expiry;
  `role_at_least` implements the `viewer<editor<admin<owner` ordering; `authenticate` resolves a
  seeded user→membership; hermetic.
