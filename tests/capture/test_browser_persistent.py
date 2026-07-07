"""Hermetic tests for `BrowserSession`'s persistent-profile support (M5 local browser capture).

These never launch a real browser: `playwright.async_api.async_playwright` is monkeypatched with
a fake that records which chromium entry point (`launch` vs `launch_persistent_context`) was used,
so the additive persistent-profile branch is verified without browser binaries. The ephemeral
(live-fleet) path is asserted to be structurally unchanged (still `launch()` + `new_context()` +
`new_page()`), guarding the "byte-for-byte unchanged when `user_data_dir=None`" contract.
"""

from __future__ import annotations

from typing import Any

import pytest

from gw_geo.capture.base import CapturePage
from gw_geo.capture.browser import BrowserSession


class _FakeKeyboard:
    def __init__(self, calls: list[tuple[str, str]]) -> None:
        self._calls = calls

    async def type(self, text: str, delay: float | None = None) -> None:
        self._calls.append(("type", text))

    async def press(self, key: str) -> None:
        self._calls.append(("press", key))


class _FakeLocator:
    """A selector containing "absent" is treated as not present (click raises)."""

    def __init__(self, selector: str) -> None:
        self._selector = selector

    @property
    def first(self) -> _FakeLocator:
        return self

    async def click(self, timeout: float | None = None) -> None:
        if "absent" in self._selector:
            raise RuntimeError(f"no element for {self._selector}")


class _FakePage:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.keyboard = _FakeKeyboard(self.calls)
        self.url = "https://surface.example/x"
        self.goto_url: str | None = None

    async def goto(self, url: str, wait_until: str | None = None) -> None:
        self.goto_url = url

    async def wait_for_load_state(self, state: str) -> None:
        self.calls.append(("wait_load", state))

    async def content(self) -> str:
        return "<html>answer</html>"

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(selector)

    async def inner_text(self, selector: str) -> str:
        if "absent" in selector:
            raise RuntimeError("not found")
        return "the settled answer"


class _FakeContext:
    def __init__(self, pages: list[_FakePage] | None = None) -> None:
        self.pages: list[_FakePage] = pages if pages is not None else []
        self.added_cookies: Any = None
        self.closed = False

    async def add_cookies(self, cookies: Any) -> None:
        self.added_cookies = cookies

    async def new_page(self) -> _FakePage:
        page = _FakePage()
        self.pages = [*self.pages, page]
        return page

    async def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self) -> None:
        self.context: _FakeContext | None = None
        self.new_context_kwargs: dict[str, Any] = {}
        self.closed = False

    async def new_context(self, **kwargs: Any) -> _FakeContext:
        self.new_context_kwargs = kwargs
        self.context = _FakeContext()
        return self.context

    async def close(self) -> None:
        self.closed = True


class _FakeChromium:
    def __init__(self, record: dict[str, Any]) -> None:
        self._record = record

    async def launch(self, **kwargs: Any) -> _FakeBrowser:
        self._record["launch"] = kwargs
        browser = _FakeBrowser()
        self._record["browser"] = browser
        return browser

    async def launch_persistent_context(
        self, user_data_dir: str, **kwargs: Any
    ) -> _FakeContext:
        self._record["persistent"] = {"user_data_dir": user_data_dir, **kwargs}
        # A real persistent context opens with one blank page already present.
        context = _FakeContext(pages=[_FakePage()])
        self._record["context"] = context
        return context


class _FakePlaywright:
    def __init__(self, record: dict[str, Any]) -> None:
        self.chromium = _FakeChromium(record)
        self._record = record

    async def stop(self) -> None:
        self._record["stopped"] = True


class _FakeAsyncPlaywright:
    def __init__(self, record: dict[str, Any]) -> None:
        self._record = record

    async def start(self) -> _FakePlaywright:
        return _FakePlaywright(self._record)


@pytest.fixture
def record(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch the lazily-imported `async_playwright` with a recording fake (no real browser)."""
    rec: dict[str, Any] = {}
    monkeypatch.setattr(
        "playwright.async_api.async_playwright", lambda: _FakeAsyncPlaywright(rec)
    )
    return rec


async def test_persistent_profile_uses_launch_persistent_context(record: dict[str, Any]) -> None:
    async with BrowserSession(
        user_data_dir="/tmp/profile", channel="chrome", headless=True
    ) as session:
        assert "persistent" in record  # took the persistent branch
        assert "launch" not in record  # NOT the ephemeral launch()+new_context() path
        persistent = record["persistent"]
        assert persistent["user_data_dir"] == "/tmp/profile"
        assert persistent["channel"] == "chrome"
        assert persistent["headless"] is True
        # Reuses the context's already-open first page rather than opening a second.
        assert session._page is record["context"].pages[0]

    # Teardown: the persistent context is closed and Playwright stopped; there is no separate
    # Browser object to close on this path.
    assert record["context"].closed is True
    assert record["stopped"] is True
    assert "browser" not in record


async def test_persistent_profile_omits_channel_when_none(record: dict[str, Any]) -> None:
    async with BrowserSession(user_data_dir="/tmp/profile", channel=None, headless=False):
        pass
    assert "channel" not in record["persistent"]
    assert record["persistent"]["headless"] is False


async def test_ephemeral_path_is_unchanged_when_no_user_data_dir(record: dict[str, Any]) -> None:
    async with BrowserSession(headless=True) as session:
        assert "launch" in record  # ephemeral launch() path
        assert "persistent" not in record
        assert record["launch"]["headless"] is True
        assert session._page is not None

    assert record["browser"].closed is True
    assert record["browser"].context.closed is True
    assert record["stopped"] is True


async def test_submit_default_returns_capture_page(record: dict[str, Any]) -> None:
    async with BrowserSession(headless=True) as session:
        page = await session.submit("best crm")
    assert isinstance(page, CapturePage)
    assert page.html == "<html>answer</html>"
    assert page.final_url == "https://surface.example/x"


async def test_submit_with_wait_for_settles_and_returns(record: dict[str, Any]) -> None:
    """`wait_for` triggers the bounded streaming-settle wait; a present selector settles fast."""
    async with BrowserSession(headless=True) as session:
        page = await session.submit(
            "best crm", wait_for="#answer", settle_timeout_ms=200.0, settle_poll_ms=5.0
        )
    assert isinstance(page, CapturePage)
    assert page.html == "<html>answer</html>"


async def test_submit_wait_for_absent_selector_degrades_without_raising(
    record: dict[str, Any],
) -> None:
    """An answer selector that never matches must time out quietly, never raise."""
    async with BrowserSession(headless=True) as session:
        page = await session.submit(
            "best crm", wait_for="#absent", settle_timeout_ms=50.0, settle_poll_ms=5.0
        )
    assert isinstance(page, CapturePage)


async def test_click_if_present_best_effort(record: dict[str, Any]) -> None:
    async with BrowserSession(headless=True) as session:
        assert await session.click_if_present("#composer") is True
        assert await session.click_if_present("#absent-1", "#absent-2") is False
        # First matching selector wins; a leading miss falls through to the next candidate.
        assert await session.click_if_present("#absent", "#present") is True
