"""Live CaptureClient: composes ProxyPool + AccountPool + BrowserSession (m1-design.md S3.1).

`LiveCaptureClient.fetch` is the real implementation of the `CaptureClient` seam (M1-T07): it
acquires a geo-matched `Proxy` (M1-T09) and a persona-matched, authenticated `Account` (M1-T10),
opens a `BrowserSession`-like session (via `session_factory`, defaulting to the real
`capture.browser.BrowserSession`) wired with that proxy + the account's cookies + a realistic
anti-bot user-agent (`capture.antibot.pick_user_agent`), navigates to the surface and submits the
query, and returns the resulting `CapturePage`.

Failure attribution + rotation: a failure constructing/opening the session (before any query is
submitted) is attributed to the *proxy* -- most likely a dead/blocked network path -- and backs
it off via `ProxyPool.mark_unhealthy`; a failure *submitting* the query on an already-open session
is attributed to the *account* -- an authenticated session going bad mid-flight reads as that
account being flagged/logged out -- and permanently excludes it via `AccountPool.mark_banned`.
Either way, the *other* resource is released normally. Both are always released/marked in a
`finally` (never leaked, even on an unexpected exception), and `fetch` acquires a fresh
proxy/account and retries, up to `max_attempts` times, re-raising the original (unwrapped) error
once attempts are exhausted.

Only unit-tested with fakes here (hermetic, `tests/capture/test_live.py`, injecting a fake
`session_factory`): the real Playwright + proxy + account fleet path is validated separately by
`tests/capture/test_live_fleet.py`, marked `@pytest.mark.live` and deselected by default. Importing
this module does not import Playwright -- `capture.browser.BrowserSession` itself only imports
`playwright.async_api` lazily, inside its `__aenter__` -- so the default suite never needs a
browser.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from gw_geo.capture.account_pool import Account, AccountPool
from gw_geo.capture.antibot import pick_user_agent
from gw_geo.capture.base import CapturePage
from gw_geo.capture.browser import BrowserSession
from gw_geo.capture.proxy_pool import Proxy, ProxyPool

# The three Playwright surfaces this fleet drives (m1-design.md S3.2) and the URL each capture
# starts from -- `session.submit()` does the rest (type the query, press Enter, wait, return the
# rendered page), so no surface needs its own bespoke navigation beyond a starting URL.
_SURFACE_START_URLS: dict[str, str] = {
    "google_ai_overviews": "https://www.google.com/",
    "chatgpt": "https://chatgpt.com/",
    "grok": "https://grok.com/",
}

_DEFAULT_MAX_ATTEMPTS = 3


class _CaptureSession(Protocol):
    """Structural shape a `session_factory` must return -- matches `BrowserSession`."""

    async def __aenter__(self) -> _CaptureSession: ...
    async def __aexit__(self, *exc: object) -> None: ...
    async def open(self, url: str) -> None: ...
    async def submit(self, query: str) -> CapturePage: ...


SessionFactory = Callable[..., _CaptureSession]


def _default_session_factory(
    *, proxy: Proxy | None, cookies: list[dict[str, Any]], user_agent: str, headless: bool
) -> BrowserSession:
    """Build the real `BrowserSession` -- the default `session_factory` (tests inject a fake)."""
    return BrowserSession(headless=headless, proxy=proxy, cookies=cookies, user_agent=user_agent)


class _ProxyAttemptError(Exception):
    """Internal: this attempt failed constructing/opening the session -- attributed to the proxy."""


class _AccountAttemptError(Exception):
    """Internal: this attempt failed submitting the query on an open session -- attributed to
    the account (an authenticated session going bad mid-flight reads as that account being
    flagged/logged out)."""


def _start_url(surface: str) -> str:
    try:
        return _SURFACE_START_URLS[surface]
    except KeyError:
        raise ValueError(
            f"LiveCaptureClient has no known start URL for surface={surface!r}"
        ) from None


def _unwrap(error: Exception) -> Exception:
    """Return the original error `raise ... from cause` attached to `error`, if any."""
    cause = error.__cause__
    return cause if isinstance(cause, Exception) else error


class LiveCaptureClient:
    """The real `CaptureClient`: composes `ProxyPool` + `AccountPool` + a `BrowserSession`."""

    def __init__(
        self,
        *,
        proxies: ProxyPool,
        accounts: AccountPool,
        headless: bool = True,
        session_factory: SessionFactory | None = None,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._proxies = proxies
        self._accounts = accounts
        self._headless = headless
        self._session_factory: SessionFactory = session_factory or _default_session_factory
        self._max_attempts = max_attempts

    async def fetch(
        self, query: str, *, surface: str, geo: str, persona: str | None
    ) -> CapturePage:
        """Acquire a proxy + account, capture `query` against `surface`, always release both.

        Retries up to `max_attempts` times, rotating away from whichever resource an attempt's
        failure is attributed to (see module docstring), and re-raises the original error once
        every attempt is exhausted.
        """
        start_url = _start_url(surface)

        last_error: Exception | None = None
        for _ in range(self._max_attempts):
            proxy = self._proxies.acquire(geo)
            try:
                account = self._accounts.acquire(surface=surface, persona=persona)
            except Exception:
                self._proxies.release(proxy)
                raise

            proxy_failed = False
            account_failed = False
            try:
                return await self._attempt(
                    proxy=proxy,
                    account=account,
                    query=query,
                    surface=surface,
                    start_url=start_url,
                )
            except _ProxyAttemptError as exc:
                proxy_failed = True
                last_error = _unwrap(exc)
            except _AccountAttemptError as exc:
                account_failed = True
                last_error = _unwrap(exc)
            finally:
                if proxy_failed:
                    self._proxies.mark_unhealthy(proxy)
                else:
                    self._proxies.release(proxy)
                if account_failed:
                    self._accounts.mark_banned(account)
                else:
                    self._accounts.release(account)

        raise last_error if last_error is not None else RuntimeError(
            "LiveCaptureClient.fetch: exhausted max_attempts without a result or error"
        )

    async def _attempt(
        self,
        *,
        proxy: Proxy,
        account: Account,
        query: str,
        surface: str,
        start_url: str,
    ) -> CapturePage:
        """Run one open+submit attempt, tagging any failure with which resource caused it."""
        user_agent = pick_user_agent(surface)
        try:
            session = self._session_factory(
                proxy=proxy,
                cookies=account.cookies,
                user_agent=user_agent,
                headless=self._headless,
            )
        except Exception as exc:
            raise _ProxyAttemptError(str(exc)) from exc

        try:
            async with session as opened:
                try:
                    await opened.open(start_url)
                except Exception as exc:
                    raise _ProxyAttemptError(str(exc)) from exc
                try:
                    return await opened.submit(query)
                except Exception as exc:
                    raise _AccountAttemptError(str(exc)) from exc
        except (_ProxyAttemptError, _AccountAttemptError):
            raise
        except Exception as exc:
            raise _ProxyAttemptError(str(exc)) from exc
