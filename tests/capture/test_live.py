"""Hermetic spec tests for `LiveCaptureClient` (docs/tasks/M1-T16-live-capture-fleet.md).

Every test here injects a fake `session_factory` -- never a real `BrowserSession` -- so the
orchestration (acquire -> open -> submit -> release, and the failure/retry/rotation paths) is
exercised without a browser, proxy, or account ever touching the network. The real fleet path
(default `session_factory`, real Playwright) is validated separately in
`tests/capture/test_live_fleet.py`, marked `@pytest.mark.live`.

`test_fetch_acquires_and_releases_and_returns_page` is the task spec's given test, reformatted
from its compact literal spacing into ruff-clean multi-line form (same precedent as
`tests/capture/test_base.py`) -- every assertion is identical to the spec. The remaining tests
pin down the failure-attribution/rotation/release contract from the task's "Interface" section
that the spec's single golden-path test doesn't otherwise exercise.
"""

import pytest

from gw_geo.capture.account_pool import Account, AccountPool, NoAccountAvailable
from gw_geo.capture.base import CapturePage, CaptureClient
from gw_geo.capture.live import LiveCaptureClient
from gw_geo.capture.proxy_pool import Proxy, ProxyPool


class FakeSession:
    def __init__(self, **kw):
        self.kw = kw

    async def __aenter__(self):
        return self

    async def __aexit__(self, *e):
        return None

    async def open(self, url):
        self.opened = url

    async def submit(self, query):
        return CapturePage(html="<div>answer</div>", final_url="https://chatgpt.com/c/1")


async def test_fetch_acquires_and_releases_and_returns_page():
    proxies = ProxyPool([Proxy(id="p1", url="http://a", geo="us")], now=lambda: 0.0)
    accounts = AccountPool([Account(id="a1", surface="chatgpt", persona="smb_buyer")])
    client = LiveCaptureClient(
        proxies=proxies, accounts=accounts, session_factory=lambda **kw: FakeSession(**kw)
    )
    assert isinstance(client, CaptureClient)
    page = await client.fetch("best crm", surface="chatgpt", geo="us", persona="smb_buyer")
    assert page.html == "<div>answer</div>"
    assert proxies.stats()["in_use"] == 0  # released in finally
    assert accounts.stats()["in_use"] == 0


async def test_fetch_wires_proxy_cookies_and_user_agent_into_the_session():
    proxies = ProxyPool([Proxy(id="p1", url="http://a", geo="us")], now=lambda: 0.0)
    accounts = AccountPool(
        [Account(id="a1", surface="chatgpt", persona="smb_buyer", cookies=[{"k": "v"}])]
    )
    seen: dict = {}

    def factory(**kw):
        seen.update(kw)
        return FakeSession(**kw)

    client = LiveCaptureClient(proxies=proxies, accounts=accounts, session_factory=factory)
    await client.fetch("best crm", surface="chatgpt", geo="us", persona="smb_buyer")

    assert seen["proxy"].id == "p1"
    assert seen["cookies"] == [{"k": "v"}]
    assert isinstance(seen["user_agent"], str) and seen["user_agent"]
    assert seen["headless"] is True


async def test_fetch_rejects_unknown_surface_without_touching_pools():
    proxies = ProxyPool([Proxy(id="p1", url="http://a", geo="us")], now=lambda: 0.0)
    accounts = AccountPool([Account(id="a1", surface="chatgpt", persona="smb_buyer")])
    client = LiveCaptureClient(
        proxies=proxies, accounts=accounts, session_factory=lambda **kw: FakeSession(**kw)
    )

    with pytest.raises(ValueError, match="surface"):
        await client.fetch("q", surface="carrier_pigeon", geo="us", persona=None)

    assert proxies.stats() == {"total": 1, "healthy": 1, "in_use": 0}
    assert accounts.stats() == {"total": 1, "banned": 0, "in_use": 0}


async def test_fetch_releases_proxy_when_account_acquire_fails():
    proxies = ProxyPool([Proxy(id="p1", url="http://a", geo="us")], now=lambda: 0.0)
    accounts = AccountPool([])  # nothing configured for chatgpt/smb_buyer
    client = LiveCaptureClient(
        proxies=proxies, accounts=accounts, session_factory=lambda **kw: FakeSession(**kw)
    )

    with pytest.raises(NoAccountAvailable):
        await client.fetch("best crm", surface="chatgpt", geo="us", persona="smb_buyer")

    assert proxies.stats()["in_use"] == 0


async def test_fetch_retries_with_a_rotated_proxy_after_an_open_failure():
    """A failure opening the session (before any query is submitted) is proxy-attributed:
    the failing proxy is marked unhealthy (backed off) and a fresh proxy is tried next."""
    attempts: list[str] = []

    class FlakyOpenSession:
        def __init__(self, **kw):
            self.kw = kw

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return None

        async def open(self, url):
            attempts.append(self.kw["proxy"].id)
            if self.kw["proxy"].id == "p1":
                raise RuntimeError("connection refused")

        async def submit(self, query):
            return CapturePage(html="<div>ok</div>", final_url="https://chatgpt.com/c/2")

    proxies = ProxyPool(
        [Proxy(id="p1", url="http://a", geo="us"), Proxy(id="p2", url="http://b", geo="us")],
        now=lambda: 0.0,
    )
    accounts = AccountPool([Account(id="a1", surface="chatgpt", persona="smb_buyer")])
    client = LiveCaptureClient(
        proxies=proxies, accounts=accounts, session_factory=lambda **kw: FlakyOpenSession(**kw)
    )

    page = await client.fetch("best crm", surface="chatgpt", geo="us", persona="smb_buyer")

    assert page.html == "<div>ok</div>"
    assert attempts == ["p1", "p2"]
    assert proxies.stats()["in_use"] == 0
    assert accounts.stats()["in_use"] == 0
    assert proxies.acquire("us").id == "p2"  # p1 still backed off from mark_unhealthy


async def test_fetch_retries_with_a_rotated_account_after_a_submit_failure():
    """A failure submitting the query on an already-open session is account-attributed: the
    failing account is permanently banned and a fresh account is tried next; the proxy that
    worked fine up to that point is released normally (not marked unhealthy)."""

    class FlakySubmitSession:
        def __init__(self, **kw):
            self.kw = kw

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return None

        async def open(self, url):
            return None

        async def submit(self, query):
            if self.kw["cookies"] == [{"acct": "a1"}]:
                raise RuntimeError("logged out")
            return CapturePage(html="<div>ok</div>", final_url="https://chatgpt.com/c/3")

    proxies = ProxyPool([Proxy(id="p1", url="http://a", geo="us")], now=lambda: 0.0)
    accounts = AccountPool(
        [
            Account(id="a1", surface="chatgpt", persona="smb_buyer", cookies=[{"acct": "a1"}]),
            Account(id="a2", surface="chatgpt", persona="smb_buyer", cookies=[{"acct": "a2"}]),
        ]
    )
    client = LiveCaptureClient(
        proxies=proxies, accounts=accounts, session_factory=lambda **kw: FlakySubmitSession(**kw)
    )

    page = await client.fetch("best crm", surface="chatgpt", geo="us", persona="smb_buyer")

    assert page.html == "<div>ok</div>"
    # Only one proxy exists: if it had been marked unhealthy (rather than released) after the
    # first, account-attributed failure, the second attempt could never have acquired it.
    assert proxies.stats() == {"total": 1, "healthy": 1, "in_use": 0}
    assert accounts.stats() == {"total": 2, "banned": 1, "in_use": 0}


async def test_fetch_raises_the_original_error_after_exhausting_retries():
    class AlwaysFailsSession:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return None

        async def open(self, url):
            raise RuntimeError("proxy dead")

        async def submit(self, query):
            raise AssertionError("must not reach submit if open failed")

    proxies = ProxyPool(
        [Proxy(id="p1", url="http://a", geo="us"), Proxy(id="p2", url="http://b", geo="us")],
        now=lambda: 0.0,
    )
    accounts = AccountPool([Account(id="a1", surface="chatgpt", persona="smb_buyer")])
    client = LiveCaptureClient(
        proxies=proxies,
        accounts=accounts,
        session_factory=lambda **kw: AlwaysFailsSession(**kw),
        max_attempts=2,
    )

    with pytest.raises(RuntimeError, match="proxy dead"):
        await client.fetch("best crm", surface="chatgpt", geo="us", persona="smb_buyer")

    assert proxies.stats()["in_use"] == 0
    assert accounts.stats()["in_use"] == 0


async def test_max_attempts_must_be_positive():
    proxies = ProxyPool([Proxy(id="p1", url="http://a", geo="us")], now=lambda: 0.0)
    accounts = AccountPool([Account(id="a1", surface="chatgpt", persona="smb_buyer")])
    with pytest.raises(ValueError, match="max_attempts"):
        LiveCaptureClient(proxies=proxies, accounts=accounts, max_attempts=0)
