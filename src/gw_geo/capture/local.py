"""Local, persistent-profile `CaptureClient` -- drives the user's OWN browser (M5, LOCAL-ONLY).

`LocalCaptureClient` is the local-dev alternative to `capture.live.LiveCaptureClient`: no proxy
pool, no account pool, no secrets. It opens ONE persistent Chrome/Chromium profile (via
`BrowserSession(user_data_dir=...)`) so the user's own logins -- established once with
`python -m gw_geo.cli login` -- carry the authentication. The three Playwright adapters
(`chatgpt`/`grok`/`google_ai_overviews`) consume it through the same `CaptureClient` seam, so
nothing about them changes.

A user-data-dir cannot be opened by two browser instances at once, and the measurement runner
gathers probes concurrently, so every `fetch` runs behind a single `asyncio.Lock`: the profile is
opened lazily on the first fetch and reused, and fetches serialize to browser-concurrency 1 (the
correct and simplest posture for one local profile).

Per-surface robustness (all best-effort; the consumer DOM is unstable, m1-design.md S10, and these
selectors will need live tuning): chat surfaces get a composer click before typing (their input is
not auto-focused) and a bounded wait for the answer container to stop streaming instead of relying
on `networkidle` alone; Google gets a best-effort cookie-consent dismissal. A stale selector
degrades to "no extra help", never an error.

Playwright is never imported here at module load: `BrowserSession` imports it lazily inside its
own `__aenter__`, so importing this module (e.g. from `common.wiring`) stays hermetic and the
default test suite -- which injects a fake `session_factory` -- launches no browser.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Protocol

from gw_geo.capture.base import CapturePage
from gw_geo.capture.browser import BrowserSession
from gw_geo.capture.live import _SURFACE_START_URLS

# Best-effort per-surface interaction policy (all consumer DOM -> WILL need live tuning). The chat
# composers aren't auto-focused, so we click them before typing; the answer container is polled
# until it stops streaming. Google's SERP has no streaming answer node, just a consent gate.
_COMPOSER_SELECTORS: dict[str, tuple[str, ...]] = {
    "chatgpt": ("#prompt-textarea", "div[contenteditable='true']", "textarea"),
    "grok": (
        "textarea[aria-label='Ask Grok anything']",
        "textarea",
        "div[contenteditable='true']",
    ),
}
_ANSWER_SELECTORS: dict[str, str] = {
    "chatgpt": '[data-message-author-role="assistant"]',
    "grok": '[data-testid="grok-answer"]',
}
_CONSENT_SELECTORS: dict[str, tuple[str, ...]] = {
    "google_ai_overviews": (
        "button#L2AGLb",  # "Accept all" on Google's consent interstitial
        "button[aria-label='Accept all']",
        "button[aria-label='Reject all']",
        "form[action*='consent'] button",
    ),
}


class _LocalSession(Protocol):
    """Structural shape a local `session_factory` must return -- matches `BrowserSession`."""

    async def __aenter__(self) -> _LocalSession: ...
    async def __aexit__(self, *exc: object) -> None: ...
    async def open(self, url: str) -> None: ...
    async def click_if_present(self, *selectors: str, timeout_ms: float = ...) -> bool: ...
    async def submit(
        self,
        query: str,
        *,
        wait_for: str | None = ...,
        settle_timeout_ms: float = ...,
        settle_poll_ms: float = ...,
    ) -> CapturePage: ...


LocalSessionFactory = Callable[..., _LocalSession]


def _default_session_factory(
    *, user_data_dir: str, channel: str | None, headless: bool
) -> BrowserSession:
    """Build the real persistent-profile `BrowserSession` -- no proxy, no cookies (LOCAL-ONLY)."""
    return BrowserSession(
        user_data_dir=user_data_dir,
        channel=channel,
        headless=headless,
        proxy=None,
        cookies=None,
    )


def _start_url(surface: str) -> str:
    """Resolve a surface to its capture start URL (reusing the fleet's table), else `ValueError`."""
    try:
        return _SURFACE_START_URLS[surface]
    except KeyError:
        raise ValueError(
            f"LocalCaptureClient has no known start URL for surface={surface!r}"
        ) from None


class LocalCaptureClient:
    """`CaptureClient` over ONE persistent local browser profile, serialized behind a lock."""

    def __init__(
        self,
        *,
        user_data_dir: str,
        channel: str | None = "chrome",
        headless: bool = True,
        session_factory: LocalSessionFactory | None = None,
    ) -> None:
        self._user_data_dir = user_data_dir
        self._channel = channel
        self._headless = headless
        self._session_factory: LocalSessionFactory = session_factory or _default_session_factory
        self._lock = asyncio.Lock()
        self._session: _LocalSession | None = None

    async def fetch(
        self, query: str, *, surface: str, geo: str, persona: str | None
    ) -> CapturePage:
        """Capture `query` against `surface` on the local profile; `geo`/`persona` are ignored.

        (Local capture drives the user's own single browser/login -- there is no proxy geo or
        multi-account persona to select -- so those args are accepted for `CaptureClient`
        compatibility and intentionally unused.)
        """
        start_url = _start_url(surface)  # raises before opening a browser on an unknown surface
        async with self._lock:  # a user-data-dir can't be shared across concurrent opens
            session = await self._ensure_open()
            await session.open(start_url)

            composer = _COMPOSER_SELECTORS.get(surface)
            if composer:
                await session.click_if_present(*composer)
            consent = _CONSENT_SELECTORS.get(surface)
            if consent:
                await session.click_if_present(*consent)

            return await session.submit(query, wait_for=_ANSWER_SELECTORS.get(surface))

    async def _ensure_open(self) -> _LocalSession:
        """Lazily open the single persistent session on first use; reuse it thereafter."""
        if self._session is None:
            session = self._session_factory(
                user_data_dir=self._user_data_dir,
                channel=self._channel,
                headless=self._headless,
            )
            await session.__aenter__()
            self._session = session
        return self._session

    async def aclose(self) -> None:
        """Close the persistent session if it was opened (idempotent). Optional teardown.

        The `CaptureClient` seam has no lifecycle method, so the measurement runner never calls
        this -- a short-lived CLI run simply exits and the browser subprocess is reaped. It exists
        for a long-lived host that wants to release the profile explicitly, and for tests.
        """
        async with self._lock:
            if self._session is not None:
                await self._session.__aexit__(None, None, None)
                self._session = None


async def run_login_session(
    *, user_data_dir: str, channel: str | None, start_url: str
) -> None:
    """Open the persistent profile HEADED at `start_url` and block until the user closes it.

    The one-time login helper behind `python -m gw_geo.cli login`: the user signs in to the
    surface with their own credentials, and the cookies persist in `user_data_dir` for subsequent
    (headless) `LocalCaptureClient` captures. This legitimately drives a REAL browser, so it is
    kept out of the default test path (the CLI imports it by name so a unit test patches it).

    Playwright is imported lazily here (never at module load) exactly like `BrowserSession`, so
    importing this module stays hermetic.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir, headless=False, channel=channel
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(start_url, wait_until="domcontentloaded")
            closed = asyncio.Event()
            # Playwright invokes the "close" handler with the context; accept and ignore it.
            context.on("close", lambda _ctx: closed.set())
            await closed.wait()  # block until the user closes the browser window
        finally:
            # The user closing the window may have already closed the context; closing an
            # already-closed context is not something we want to raise on.
            with contextlib.suppress(Exception):
                await context.close()
