"""Real-fleet integration test for `LiveCaptureClient` (docs/tasks/M1-T16-live-capture-fleet.md).

Marked `@pytest.mark.live` -- deselected by default (the project's standard invocation is
`pytest -m "not live"`, per `docs/tasks/M1-README.md` -> "Conventions"; the `live` marker is
registered in `pyproject.toml`). This test drives the REAL fleet: the default `session_factory`
(the real `capture.browser.BrowserSession`, i.e. a real Playwright browser), a real outbound
proxy, and a real authenticated account/session -- against a real consumer surface (chatgpt.com,
grok.com, or Google AI Overviews). It never runs in the default suite or CI.

Prerequisites to run this test manually (`pytest -m live tests/capture/test_live_fleet.py`), none
of which are needed for the default suite:

1. Browser binaries: `playwright install chromium` (the `playwright` *package* is a normal
   dependency and is already installed; the browser binaries it drives are a separate, one-time
   local/deploy step -- see the `playwright` dependency comment in `pyproject.toml`).
2. Real credentials, supplied via environment variables and never committed to this repo (per
   `capture/account_pool.py`'s white-hat/no-secrets-in-repo policy):
     - `GEO_LIVE_PROXY_URL`             e.g. "http://user:pass@host:port"        (required)
     - `GEO_LIVE_PROXY_GEO`             e.g. "us"                                (default "us")
     - `GEO_LIVE_SURFACE`               "chatgpt" | "grok" | "google_ai_overviews" (default "chatgpt")
     - `GEO_LIVE_PERSONA`               optional persona label                   (default None)
     - `GEO_LIVE_ACCOUNT_COOKIES_JSON`  a JSON `list[dict]` of session cookies    (default "[]",
                                          i.e. no login -- fine for a surface that needs none)
3. Network egress from the test host to both the proxy and the target surface.

Without `GEO_LIVE_PROXY_URL` set, this test SKIPS (even when explicitly selected with `-m live`)
rather than failing, so running `-m live` in an unconfigured environment reports a clear skip
reason instead of a confusing connection error.
"""

import json
import os

import pytest

from gw_geo.capture.account_pool import Account, AccountPool
from gw_geo.capture.live import LiveCaptureClient
from gw_geo.capture.proxy_pool import Proxy, ProxyPool

pytestmark = pytest.mark.live


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        pytest.skip(f"live fleet test requires {name} to be set -- see module docstring")
    return value


async def test_live_capture_client_fetches_a_real_surface() -> None:
    proxy_url = _require_env("GEO_LIVE_PROXY_URL")
    geo = os.environ.get("GEO_LIVE_PROXY_GEO", "us")
    surface = os.environ.get("GEO_LIVE_SURFACE", "chatgpt")
    persona = os.environ.get("GEO_LIVE_PERSONA") or None
    cookies = json.loads(os.environ.get("GEO_LIVE_ACCOUNT_COOKIES_JSON", "[]"))

    proxies = ProxyPool([Proxy(id="live-proxy", url=proxy_url, geo=geo)])
    accounts = AccountPool(
        [Account(id="live-account", surface=surface, persona=persona, cookies=cookies)]
    )
    # No `session_factory` override: this exercises the real `capture.browser.BrowserSession`
    # (real Playwright, real proxy, real account) end to end.
    client = LiveCaptureClient(proxies=proxies, accounts=accounts, headless=True)

    page = await client.fetch(
        "best crm for a 10-person startup", surface=surface, geo=geo, persona=persona
    )

    assert page.html
    assert page.final_url.startswith("http")
    assert proxies.stats()["in_use"] == 0
    assert accounts.stats()["in_use"] == 0
