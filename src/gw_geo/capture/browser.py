"""Real Playwright wrapper for the capture fleet (m1-design.md S3.1).

Composed by `LiveCaptureClient` (M1-T16) together with `ProxyPool` (M1-T09) and `AccountPool`
(M1-T10) to implement `CaptureClient.fetch` against live consumer surfaces (Google AI Overviews,
consumer ChatGPT, Grok). This class is not exercised by the default test suite -- only under
`@pytest.mark.live` (M1-T16), against a real browser.

`playwright.async_api` is imported lazily, inside methods, so importing this module never pulls
Playwright into memory or requires browser binaries (`playwright install`) to be present -- that
install step is a deploy/live prerequisite, not a default-suite one. The `TYPE_CHECKING` import
below is erased at runtime (see `from __future__ import annotations`) and exists purely so mypy
can type-check attributes/return values against Playwright's real classes.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from typing import TYPE_CHECKING, Any

from gw_geo.capture.base import CapturePage
from gw_geo.capture.proxy_pool import Proxy

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright

# A streamed answer's text length jitters by a few characters even after it has effectively
# settled (trailing whitespace, a cursor glyph, a re-rendered token). Treat two polls whose
# lengths are within this many characters as "the same length" so that jitter doesn't reset the
# stability count and stall `_wait_for_answer_stable` until timeout.
_STABLE_TOLERANCE_CHARS = 16


class BrowserSession:
    """Async Playwright browser/context/page wired with a proxy, cookies, and anti-bot posture.

    Use as an async context manager::

        async with BrowserSession(headless=True) as session:
            await session.open("https://example.com/search")
            page = await session.submit("best crm for smb")
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        proxy: Proxy | None = None,
        cookies: list[dict[str, Any]] | None = None,
        user_agent: str | None = None,
        user_data_dir: str | None = None,
        channel: str | None = None,
    ) -> None:
        self._headless = headless
        self._proxy = proxy
        self._cookies = cookies
        self._user_agent = user_agent
        # Persistent-profile mode (M5 local browser capture): when `user_data_dir` is set, the
        # session drives ONE durable Chrome/Chromium profile on disk (the user's own logins), via
        # `launch_persistent_context`, instead of the ephemeral proxy/cookie fleet context. When
        # it is None, this class behaves exactly as before (the live-fleet path is untouched).
        # `channel` selects a branded browser build ("chrome"/"msedge"); None uses bundled Chromium.
        self._user_data_dir = user_data_dir
        self._channel = channel
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def __aenter__(self) -> BrowserSession:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()

        if self._user_data_dir is not None:
            # Persistent-profile path (LocalCaptureClient). `launch_persistent_context` returns the
            # context directly -- there is no separate `Browser` object -- so `self._browser` stays
            # None and `__aexit__` closes only the context. `channel`/`proxy`/`user_agent` are
            # passed through when set; local capture leaves proxy/user_agent None.
            persistent_kwargs: dict[str, Any] = {"headless": self._headless}
            if self._channel is not None:
                persistent_kwargs["channel"] = self._channel
            if self._proxy is not None:
                persistent_kwargs["proxy"] = {"server": self._proxy.url}
            if self._user_agent is not None:
                persistent_kwargs["user_agent"] = self._user_agent
            self._context = await self._playwright.chromium.launch_persistent_context(
                self._user_data_dir, **persistent_kwargs
            )
            if self._cookies:
                await self._context.add_cookies(self._cookies)  # type: ignore[arg-type]
            # Reuse the profile's already-open page (a persistent context starts with one) rather
            # than opening a second blank tab; fall back to creating one if somehow empty.
            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()
            return self

        launch_kwargs: dict[str, Any] = {"headless": self._headless}
        if self._proxy is not None:
            launch_kwargs["proxy"] = {"server": self._proxy.url}
        self._browser = await self._playwright.chromium.launch(**launch_kwargs)

        context_kwargs: dict[str, Any] = {}
        if self._user_agent is not None:
            context_kwargs["user_agent"] = self._user_agent
        self._context = await self._browser.new_context(**context_kwargs)
        if self._cookies:
            # `cookies` is intentionally plain `list[dict[str, Any]]` in our public API (it comes
            # from the account/session store as plain JSON, per m1-design.md S3.1) rather than
            # Playwright's internal `SetCookieParam` TypedDict; the shapes are compatible at
            # runtime, so this is a deliberate, narrow mypy suppression.
            await self._context.add_cookies(self._cookies)  # type: ignore[arg-type]

        self._page = await self._context.new_page()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def open(self, url: str) -> None:
        """Navigate to `url`, then pace like a human before interacting (anti-bot timing)."""
        page = self._require_page()
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(0.5, 1.5))

    async def submit(
        self,
        query: str,
        *,
        wait_for: str | None = None,
        settle_timeout_ms: float = 15000.0,
        settle_poll_ms: float = 350.0,
    ) -> CapturePage:
        """Type `query` into the active surface, submit it, and return the rendered result.

        Types the query, presses Enter, then delegates to `snapshot` to settle and capture. The
        surface-navigation shape is the live fleet's (type, Enter, settle, capture); the only
        behavioral change is that `snapshot`'s `networkidle` wait is now best-effort/bounded rather
        than an unbounded 30s wait that ChatGPT (which holds streaming/telemetry sockets open so
        `networkidle` never fires) would fail on. When `wait_for` is a selector (the local
        persistent-profile path passes the answer container for streaming surfaces), `snapshot`
        additionally waits -- best-effort, bounded by `settle_timeout_ms` -- for that container's
        text to stop growing before capturing, so a mid-stream fragment isn't captured as the final
        answer.
        """
        page = self._require_page()
        await asyncio.sleep(random.uniform(0.2, 0.6))
        await page.keyboard.type(query, delay=random.uniform(20, 60))
        await page.keyboard.press("Enter")
        return await self.snapshot(
            wait_for=wait_for,
            settle_timeout_ms=settle_timeout_ms,
            settle_poll_ms=settle_poll_ms,
        )

    async def snapshot(
        self,
        *,
        wait_for: str | None = None,
        settle_timeout_ms: float = 15000.0,
        settle_poll_ms: float = 350.0,
    ) -> CapturePage:
        """Settle the current page and capture its HTML + final URL, without typing anything.

        The no-type capture path: `submit` calls it after typing a query, and the Google AI-Mode
        local path calls it directly after navigating straight to a results URL (there is nothing
        to type).

        The `networkidle` wait is deliberately best-effort and short (bounded to 5s and never
        allowed to raise): many consumer surfaces -- ChatGPT especially -- hold streaming/telemetry
        sockets open so `networkidle` never fires, and an unbounded wait there would time out and
        fail the whole capture before the answer is ever read. A short bounded wait still helps a
        static page settle, while a miss simply proceeds to the answer wait. When `wait_for` is a
        selector, additionally wait -- best-effort, bounded by `settle_timeout_ms` -- for that
        container's text to stop growing before capturing.
        """
        page = self._require_page()
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=5000)
        if wait_for is not None:
            await self._wait_for_answer_stable(
                page, wait_for, timeout_ms=settle_timeout_ms, poll_ms=settle_poll_ms
            )
        html = await page.content()
        return CapturePage(html=html, final_url=page.url)

    async def click_if_present(self, *selectors: str, timeout_ms: float = 1500.0) -> bool:
        """Best-effort: click the first of `selectors` that is present; return whether one clicked.

        Local-capture robustness helper (never used on the live-fleet path): focuses a chat
        composer before typing, or dismisses a cookie-consent button. Every selector is unstable
        consumer DOM, so a miss/timeout on all candidates degrades to `False` rather than raising.
        """
        page = self._require_page()
        for selector in selectors:
            try:
                await page.locator(selector).first.click(timeout=timeout_ms)
                return True
            except Exception:
                continue
        return False

    async def _wait_for_answer_stable(
        self,
        page: Page,
        selector: str,
        *,
        timeout_ms: float,
        poll_ms: float,
        stable_needed: int = 2,
    ) -> None:
        """Poll `selector`'s text length until it holds steady across `stable_needed` polls.

        "Steady" means non-empty and within `_STABLE_TOLERANCE_CHARS` of the previous poll: a
        streamed answer briefly plateaus mid-stream (a token boundary or network hiccup), and the
        container can be momentarily static before generation even starts, so a *single* stable
        pair returns too early -- capturing a mid-stream fragment or an empty shell. Requiring
        `stable_needed` consecutive steady polls rides through those plateaus.

        Best-effort and bounded: returns as soon as the answer settles, or quietly once
        `timeout_ms` elapses (a never-matching or perpetually-streaming selector must not hang or
        raise -- the caller still captures whatever has rendered).
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_ms / 1000.0
        previous = -1
        stable = 0
        while loop.time() < deadline:
            try:
                length = len(await page.inner_text(selector))
            except Exception:
                length = 0
            if length > 0 and previous >= 0 and abs(length - previous) <= _STABLE_TOLERANCE_CHARS:
                stable += 1
                if stable >= stable_needed:
                    return
            else:
                stable = 0
            previous = length
            await asyncio.sleep(poll_ms / 1000.0)

    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError(
                "BrowserSession must be entered first: `async with BrowserSession(...) as s`"
            )
        return self._page
