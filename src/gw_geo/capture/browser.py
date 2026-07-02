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
import random
from typing import TYPE_CHECKING, Any

from gw_geo.capture.base import CapturePage

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright

# `Proxy` (a geo-aware proxy descriptor with a `.url`) is defined in `gw_geo.capture.proxy_pool`
# (M1-T09), which lands in a later wave. Typed as `Any` here to avoid a forward dependency on
# that module; tighten to the real `Proxy` type once T09 merges.
Proxy = Any


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
    ) -> None:
        self._headless = headless
        self._proxy = proxy
        self._cookies = cookies
        self._user_agent = user_agent
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def __aenter__(self) -> BrowserSession:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()

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

    async def submit(self, query: str) -> CapturePage:
        """Type `query` into the active surface, submit it, and return the rendered result."""
        page = self._require_page()
        await asyncio.sleep(random.uniform(0.2, 0.6))
        await page.keyboard.type(query, delay=random.uniform(20, 60))
        await page.keyboard.press("Enter")
        await page.wait_for_load_state("networkidle")
        html = await page.content()
        return CapturePage(html=html, final_url=page.url)

    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError(
                "BrowserSession must be entered first: `async with BrowserSession(...) as s`"
            )
        return self._page
