"""Per-(surface, persona) authenticated session pool (m1-design.md S3.1, docs/tasks/M1-T10).

Composed by `LiveCaptureClient` (M1-T16) together with `ProxyPool` (M1-T09) to supply
authenticated sessions (cookies/tokens) for consumer surfaces that require sign-in (e.g.
consumer ChatGPT). Session material is never stored in this repo -- it is loaded at runtime via
an injected `SecretProvider` (backed by SSM/Secrets Manager in deploy, a fake in tests).
White-hat only (PRD NG1): this pool manages ordinary authenticated sessions and rotates them
away from bans -- it never cloaks content or serves divergent content to bots.
"""

from typing import Any, Protocol

from pydantic import BaseModel, Field


class Account(BaseModel):
    """One authenticated session (cookies/tokens) for a given `(surface, persona)`."""

    id: str
    surface: str
    persona: str | None = None
    # `list[dict[str, Any]]`, not the spec's bare `list[dict]` -- same runtime shape (plain
    # JSON-ish cookie dicts, as in `BrowserSession`'s identical `cookies` param in
    # capture/browser.py), parameterized so `mypy src/gw_geo` stays clean.
    cookies: list[dict[str, Any]] = Field(default_factory=list)
    banned: bool = False


class SecretProvider(Protocol):
    """Loads `Account` session material from an external secret store (e.g. SSM)."""

    def load_accounts(self, config_ref: str) -> list[Account]: ...


class NoAccountAvailable(Exception):
    """Raised when no free, non-banned account matches the requested `(surface, persona)`."""


class AccountPool:
    """In-memory pool of `Account` sessions with per-`(surface, persona)` acquire/release.

    Fully in-memory: callers are responsible for persisting/rotating the underlying
    credentials out of band (via `SecretProvider`). An account marked banned via
    `mark_banned` is excluded permanently for the lifetime of this pool instance.
    """

    def __init__(self, accounts: list[Account]) -> None:
        self._accounts: dict[str, Account] = {account.id: account for account in accounts}
        self._in_use: set[str] = set()
        # Monotonic per-(surface, persona) cursor, modded by the *current* candidate count on
        # each acquire -- this rotates round-robin across matching accounts even as the
        # candidate set shrinks/grows (bans, releases) between calls.
        self._next_index: dict[tuple[str, str | None], int] = {}

    @classmethod
    def from_secrets(cls, provider: SecretProvider, config_ref: str) -> "AccountPool":
        """Build a pool from accounts loaded via `provider.load_accounts(config_ref)`.

        `provider` is the only source of session material -- no credentials ever live in
        this repo.
        """
        return cls(provider.load_accounts(config_ref))

    def _available_ids(self, *, surface: str, persona: str | None) -> list[str]:
        return [
            account_id
            for account_id, account in self._accounts.items()
            if account.surface == surface
            and account.persona == persona
            and not account.banned
            and account_id not in self._in_use
        ]

    def acquire(self, *, surface: str, persona: str | None) -> Account:
        """Return a free, non-banned account matching `(surface, persona)`.

        Rotates round-robin across matching accounts on successive calls.

        Raises:
            NoAccountAvailable: no matching account is currently free.
        """
        key = (surface, persona)
        candidates = self._available_ids(surface=surface, persona=persona)
        if not candidates:
            raise NoAccountAvailable(
                f"no available account for surface={surface!r} persona={persona!r}"
            )
        index = self._next_index.get(key, 0) % len(candidates)
        chosen_id = candidates[index]
        self._next_index[key] = index + 1
        self._in_use.add(chosen_id)
        return self._accounts[chosen_id]

    def release(self, account: Account) -> None:
        """Return `account` to the pool so it can be acquired again."""
        self._in_use.discard(account.id)

    def mark_banned(self, account: Account) -> None:
        """Permanently exclude `account` from future `acquire` calls and free its slot."""
        stored = self._accounts.get(account.id)
        if stored is not None:
            stored.banned = True
        self._in_use.discard(account.id)

    def stats(self) -> dict[str, int]:
        """Return pool-wide counts: `total`, `banned`, `in_use`."""
        banned = sum(1 for account in self._accounts.values() if account.banned)
        return {
            "total": len(self._accounts),
            "banned": banned,
            "in_use": len(self._in_use),
        }
