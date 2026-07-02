# M1-T10 — AccountPool + anti-bot session material

**Depends on:** T01 (config) · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** Per-`(surface, persona)` authenticated session pool for consumer surfaces: acquire/release
a session (cookies/tokens), rotate on ban, and supply anti-bot fingerprint material (user-agent,
timing jitter). Session material is loaded from an injected secret provider — **never from the
repo**. Unit-tested with fakes; no live accounts.

**Files:**
- Create: `src/gw_geo/capture/account_pool.py`, `src/gw_geo/capture/antibot.py`
- Test: `tests/capture/test_account_pool.py`, `tests/capture/test_antibot.py`

## Interface

```python
# capture/account_pool.py
from typing import Protocol
from pydantic import BaseModel, Field

class Account(BaseModel):
    id: str
    surface: str
    persona: str | None = None
    cookies: list[dict] = Field(default_factory=list)
    banned: bool = False

class SecretProvider(Protocol):
    def load_accounts(self, config_ref: str) -> list[Account]: ...   # from SSM/secret store

class NoAccountAvailable(Exception): ...

class AccountPool:
    def __init__(self, accounts: list[Account]) -> None: ...
    @classmethod
    def from_secrets(cls, provider: SecretProvider, config_ref: str) -> "AccountPool": ...
    def acquire(self, *, surface: str, persona: str | None) -> Account: ...
    def release(self, account: Account) -> None: ...
    def mark_banned(self, account: Account) -> None: ...   # rotate out; excluded permanently
    def stats(self) -> dict[str, int]: ...
```

```python
# capture/antibot.py
def pick_user_agent(surface: str, *, rng = None) -> str: ...    # realistic UA per surface
def jitter_delay(base_ms: int, *, rng = None) -> float: ...     # humanized timing (ms)
```

- `acquire` returns a non-banned session matching `(surface, persona)`, rotating across calls;
  `NoAccountAvailable` if none. `mark_banned` excludes it. `from_secrets` pulls material via the
  injected `SecretProvider` (a fake in tests) — no credentials in the repo. `antibot` helpers are
  pure functions with an injectable `rng` for deterministic tests. White-hat only: fingerprint
  realism to avoid trivial blocking, **no cloaking/injection** (PRD NG1).

## Steps
- [ ] **1. Failing test** `tests/capture/test_account_pool.py`:

```python
import pytest
from gw_geo.capture.account_pool import Account, AccountPool, NoAccountAvailable

class FakeSecrets:
    def load_accounts(self, config_ref):
        return [Account(id="a1", surface="chatgpt", persona="smb_buyer"),
                Account(id="a2", surface="chatgpt", persona="smb_buyer")]

def test_from_secrets_and_acquire_by_surface_persona():
    pool = AccountPool.from_secrets(FakeSecrets(), "ssm://accounts")
    a = pool.acquire(surface="chatgpt", persona="smb_buyer")
    assert a.surface == "chatgpt" and a.persona == "smb_buyer"
    with pytest.raises(NoAccountAvailable):
        pool.acquire(surface="grok", persona="smb_buyer")   # no grok accounts

def test_ban_rotates_out():
    pool = AccountPool.from_secrets(FakeSecrets(), "ref")
    a = pool.acquire(surface="chatgpt", persona="smb_buyer"); pool.mark_banned(a); pool.release(a)
    b = pool.acquire(surface="chatgpt", persona="smb_buyer")
    assert b.id != a.id
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `account_pool.py` + `antibot.py` per interface. Add
  `tests/capture/test_antibot.py` asserting `pick_user_agent` / `jitter_delay` are deterministic
  under a seeded `rng` and return plausible values.
- [ ] **4. Run → pass**; mypy clean. Confirm no secret material is hardcoded anywhere.
- [ ] **5. Commit:** `feat(capture): per-surface account pool + anti-bot material`

## Acceptance
- Sessions acquired/released/rotated per `(surface, persona)`; bans rotate accounts out;
  `from_secrets` loads via an injected provider (no repo credentials); anti-bot helpers
  deterministic under a seeded rng; white-hat only; hermetic (no live accounts).
