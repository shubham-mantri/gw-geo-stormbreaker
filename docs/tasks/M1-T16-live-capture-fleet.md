# M1-T16 — Live CaptureClient (compose the fleet)

**Depends on:** T07 (seam), T09 (ProxyPool), T10 (AccountPool) · **Wave:** 3
**Suggested agent:** general-purpose (integration task — assign after T07/T09/T10 merge)

**Goal:** The real `LiveCaptureClient` that composes `ProxyPool` + `AccountPool` + `BrowserSession`
to implement `CaptureClient.fetch` (m1-design §3.1). Its wiring/orchestration logic is unit-tested
with **fakes** (hermetic); the real Playwright/proxy/account path is validated only behind
`@pytest.mark.live`, deselected by default (`-m "not live"`).

**Files:**
- Create: `src/gw_geo/capture/live.py`
- Test: `tests/capture/test_live.py` (hermetic, fakes), `tests/capture/test_live_fleet.py`
  (`@pytest.mark.live`, skipped by default)

## Interface

```python
from gw_geo.capture.base import CaptureClient, CapturePage
from gw_geo.capture.proxy_pool import ProxyPool
from gw_geo.capture.account_pool import AccountPool

class LiveCaptureClient:
    def __init__(self, *, proxies: ProxyPool, accounts: AccountPool,
                 headless: bool = True, session_factory = None) -> None: ...
        # session_factory(*, proxy, cookies, user_agent, headless) -> BrowserSession-like
        # (defaults to the real capture.browser.BrowserSession; a fake is injected in tests)
    async def fetch(self, query: str, *, surface: str, geo: str,
                    persona: str | None) -> CapturePage: ...
```

`fetch` orchestrates: `proxies.acquire(geo)` → `accounts.acquire(surface=surface, persona=persona)`
→ open a `BrowserSession` (via `session_factory`) wired with that proxy + the account cookies +
anti-bot material → navigate/submit the surface → return `CapturePage`. On failure it
`mark_unhealthy`/`mark_banned` and retries with rotation; always `release`s proxy+account in a
`finally`. Conforms to `CaptureClient`.

## Steps
- [ ] **1. Failing (hermetic) test** `tests/capture/test_live.py` (inject a fake `session_factory`):

```python
from gw_geo.capture.base import CaptureClient, CapturePage
from gw_geo.capture.live import LiveCaptureClient
from gw_geo.capture.proxy_pool import Proxy, ProxyPool
from gw_geo.capture.account_pool import Account, AccountPool

class FakeSession:
    def __init__(self, **kw): self.kw = kw
    async def __aenter__(self): return self
    async def __aexit__(self, *e): return None
    async def open(self, url): self.opened = url
    async def submit(self, query):
        return CapturePage(html="<div>answer</div>", final_url="https://chatgpt.com/c/1")

async def test_fetch_acquires_and_releases_and_returns_page():
    proxies = ProxyPool([Proxy(id="p1", url="http://a", geo="us")], now=lambda: 0.0)
    accounts = AccountPool([Account(id="a1", surface="chatgpt", persona="smb_buyer")])
    client = LiveCaptureClient(proxies=proxies, accounts=accounts,
                               session_factory=lambda **kw: FakeSession(**kw))
    assert isinstance(client, CaptureClient)
    page = await client.fetch("best crm", surface="chatgpt", geo="us", persona="smb_buyer")
    assert page.html == "<div>answer</div>"
    assert proxies.stats()["in_use"] == 0        # released in finally
    assert accounts.stats()["in_use"] == 0
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `live.py` (compose pools + session; retry-with-rotation; release in `finally`).
  Default `session_factory` builds the real `capture.browser.BrowserSession`.
- [ ] **4. Run → pass** (hermetic). Add `tests/capture/test_live_fleet.py` marked
  `@pytest.mark.live` that drives the **real** fleet against a live surface — **skipped by default**
  (`pytest -m "not live"`); register the `live` marker in `pytest.ini`/`pyproject`. Document
  `playwright install` + real proxy/account credentials as prerequisites for that path only.
- [ ] **5. Commit:** `feat(capture): live capture client composing proxy+account+browser`

## Acceptance
- `LiveCaptureClient` conforms to `CaptureClient`; hermetic test verifies acquire→submit→release with
  a fake session and clean proxy/account release; the real fleet path exists but is `@pytest.mark.live`
  and never runs in the default suite.
