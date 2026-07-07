"""Hermetic tests for `LocalCaptureClient` (M5 local persistent-profile browser capture).

Every test injects a fake session (never a real `BrowserSession`), so no browser, profile, or
network is touched. They pin: surface -> start-URL resolution, the per-surface best-effort submit
path (composer focus + streaming wait for chatgpt/grok, cookie-consent dismissal for Google), the
single lazily-opened session reused across fetches, and that the `asyncio.Lock` serializes
concurrent fetches to browser-concurrency 1. The real browser path is exercised only by the
`@pytest.mark.live` test in `test_local_live.py`, deselected by default.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gw_geo.capture import local
from gw_geo.capture.base import CapturePage, CaptureClient
from gw_geo.capture.local import LocalCaptureClient


class _Concurrency:
    """Tracks how many fetches are simultaneously inside the open..submit window."""

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def enter(self) -> None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0)  # yield: if the lock were missing, a peer fetch could interleave here

    async def exit(self) -> None:
        await asyncio.sleep(0)
        self.active -= 1


class FakeLocalSession:
    def __init__(self, *, tracker: _Concurrency | None = None, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.opened_urls: list[str] = []
        self.clicks: list[tuple[str, ...]] = []
        self.submits: list[tuple[str, str | None]] = []
        self.entered = False
        self.exited = False
        self._tracker = tracker

    async def __aenter__(self) -> FakeLocalSession:
        self.entered = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.exited = True

    async def open(self, url: str) -> None:
        if self._tracker is not None:
            await self._tracker.enter()
        self.opened_urls.append(url)

    async def click_if_present(self, *selectors: str, timeout_ms: float = 1500.0) -> bool:
        self.clicks.append(selectors)
        return True

    async def submit(
        self,
        query: str,
        *,
        wait_for: str | None = None,
        settle_timeout_ms: float = 15000.0,
        settle_poll_ms: float = 350.0,
    ) -> CapturePage:
        self.submits.append((query, wait_for))
        if self._tracker is not None:
            await self._tracker.exit()
        return CapturePage(html="<html>fake</html>", final_url="https://fake/answer")


def _factory(tracker: _Concurrency | None = None) -> Any:
    """A recording session factory; `.created` lists every session it built."""
    created: list[FakeLocalSession] = []

    def factory(**kwargs: Any) -> FakeLocalSession:
        session = FakeLocalSession(tracker=tracker, **kwargs)
        created.append(session)
        return session

    factory.created = created  # type: ignore[attr-defined]
    return factory


def _client(factory: Any) -> LocalCaptureClient:
    return LocalCaptureClient(
        user_data_dir="/tmp/profile", channel="chrome", headless=True, session_factory=factory
    )


async def test_conforms_to_capture_client_protocol() -> None:
    assert isinstance(_client(_factory()), CaptureClient)


async def test_fetch_resolves_surface_start_url_and_returns_page() -> None:
    factory = _factory()
    client = _client(factory)

    page = await client.fetch("best crm", surface="chatgpt", geo="us", persona=None)

    assert isinstance(page, CapturePage)
    assert page.html == "<html>fake</html>"
    session = factory.created[0]
    assert session.opened_urls == ["https://chatgpt.com/"]
    assert session.submits[0][0] == "best crm"
    # The session is built for the configured persistent profile (no proxy/cookies here).
    assert session.kwargs["user_data_dir"] == "/tmp/profile"
    assert session.kwargs["channel"] == "chrome"
    assert session.kwargs["headless"] is True


@pytest.mark.parametrize(
    "surface,url,answer_marker",
    [
        ("chatgpt", "https://chatgpt.com/", "assistant"),
        ("grok", "https://grok.com/", "grok"),
    ],
)
async def test_chat_surfaces_focus_composer_and_wait_for_answer(
    surface: str, url: str, answer_marker: str
) -> None:
    """chatgpt/grok: click the composer before typing, then submit with a streaming wait_for."""
    factory = _factory()
    client = _client(factory)

    await client.fetch("q", surface=surface, geo="us", persona=None)

    session = factory.created[0]
    assert session.opened_urls == [url]
    assert session.clicks, "expected a best-effort composer click before typing"
    _query, wait_for = session.submits[0]
    assert wait_for is not None and answer_marker in wait_for


async def test_google_dismisses_consent_and_submits_without_streaming_wait() -> None:
    """google_ai_overviews: best-effort dismiss cookie consent; no streaming answer container."""
    factory = _factory()
    client = _client(factory)

    await client.fetch("q", surface="google_ai_overviews", geo="us", persona=None)

    session = factory.created[0]
    assert session.opened_urls == ["https://www.google.com/"]
    assert session.clicks, "expected a best-effort cookie-consent dismissal"
    assert session.submits[0][1] is None  # no answer-container settle wait for a SERP


async def test_unknown_surface_raises_without_opening_a_session() -> None:
    factory = _factory()
    client = _client(factory)

    with pytest.raises(ValueError, match="surface"):
        await client.fetch("q", surface="carrier_pigeon", geo="us", persona=None)

    assert factory.created == []  # failed before building/opening any browser


async def test_session_is_opened_once_and_reused_across_fetches() -> None:
    factory = _factory()
    client = _client(factory)

    await client.fetch("q1", surface="chatgpt", geo="us", persona=None)
    await client.fetch("q2", surface="grok", geo="us", persona=None)
    await client.fetch("q3", surface="google_ai_overviews", geo="us", persona=None)

    assert len(factory.created) == 1  # one persistent profile, reused
    assert factory.created[0].entered is True
    assert len(factory.created[0].submits) == 3


async def test_lock_serializes_concurrent_fetches_to_one_browser() -> None:
    """A user-data-dir can't be shared across concurrent opens; the lock forces concurrency 1."""
    tracker = _Concurrency()
    factory = _factory(tracker)
    client = _client(factory)

    await asyncio.gather(
        *(client.fetch(f"q{i}", surface="chatgpt", geo="us", persona=None) for i in range(5))
    )

    assert tracker.max_active == 1
    assert len(factory.created[0].submits) == 5


async def test_aclose_exits_the_session() -> None:
    factory = _factory()
    client = _client(factory)
    await client.fetch("q", surface="chatgpt", geo="us", persona=None)

    await client.aclose()

    assert factory.created[0].exited is True
    # Idempotent: a second close (or close before any fetch) is a no-op.
    await client.aclose()


async def test_aclose_before_any_fetch_is_a_noop() -> None:
    factory = _factory()
    client = _client(factory)
    await client.aclose()
    assert factory.created == []


def test_default_session_factory_builds_persistent_browser_session() -> None:
    """The default factory wires `BrowserSession` in persistent-profile mode, no proxy/cookies."""
    from gw_geo.capture.browser import BrowserSession

    session = local._default_session_factory(
        user_data_dir="/tmp/p", channel="chrome", headless=True
    )
    assert isinstance(session, BrowserSession)
    assert session._user_data_dir == "/tmp/p"
    assert session._channel == "chrome"
    assert session._headless is True
    assert session._proxy is None
    assert session._cookies is None
