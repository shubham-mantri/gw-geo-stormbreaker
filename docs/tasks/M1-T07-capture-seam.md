# M1-T07 — CaptureClient seam + BrowserSession + fake capturer

**Depends on:** T01 (config) · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** The DI seam that keeps Playwright adapters hermetic. Define the `CapturePage` model and the
`CaptureClient` Protocol, a `BrowserSession` wrapper (real Playwright wiring, not exercised in the
default suite), and a `FakeCaptureClient` that serves recorded HTML fixtures. This is the keystone
for T11–T13 (Playwright adapters) and T16 (live fleet) — get the interface exactly right.

**Files:**
- Create: `src/gw_geo/capture/__init__.py`, `src/gw_geo/capture/base.py`,
  `src/gw_geo/capture/browser.py`
- Create: `tests/capture/__init__.py`, `tests/capture/fakes.py`
- Test: `tests/capture/test_base.py`

## Interface

```python
# capture/base.py
from typing import Any, Protocol, runtime_checkable
from pydantic import BaseModel, Field

class CapturePage(BaseModel):
    html: str
    final_url: str
    screenshots: list[str] = Field(default_factory=list)   # optional S3 refs
    meta: dict[str, Any] = Field(default_factory=dict)

@runtime_checkable
class CaptureClient(Protocol):
    async def fetch(self, query: str, *, surface: str, geo: str,
                    persona: str | None) -> CapturePage: ...
```

```python
# capture/browser.py — real Playwright wrapper (exercised only under @pytest.mark.live via T16)
class BrowserSession:
    def __init__(self, *, headless: bool = True, proxy: "Proxy | None" = None,
                 cookies: list[dict] | None = None, user_agent: str | None = None) -> None: ...
    async def __aenter__(self) -> "BrowserSession": ...
    async def __aexit__(self, *exc) -> None: ...
    async def open(self, url: str) -> None: ...       # navigate with anti-bot timing
    async def submit(self, query: str) -> "CapturePage": ...   # type/submit, return html+final_url
```

```python
# tests/capture/fakes.py — hermetic test double
class FakeCaptureClient:
    """Serves a recorded HTML fixture keyed by (surface, geo, persona)."""
    def __init__(self, pages: dict[str, CapturePage]) -> None: ...   # key = surface
    async def fetch(self, query, *, surface, geo, persona) -> CapturePage: ...
```

The `BrowserSession` may import `playwright.async_api` lazily (inside methods) so module import
stays cheap and the default suite never requires a browser.

## Steps
- [ ] **1. Failing test** `tests/capture/test_base.py`:

```python
from gw_geo.capture.base import CapturePage, CaptureClient
from tests.capture.fakes import FakeCaptureClient

async def test_fake_capture_client_conforms_and_serves():
    pages = {"google_ai_overviews": CapturePage(html="<div>hi</div>",
                                                final_url="https://www.google.com/search?q=x")}
    c = FakeCaptureClient(pages)
    assert isinstance(c, CaptureClient)                     # runtime-checkable Protocol
    page = await c.fetch("best crm", surface="google_ai_overviews", geo="us", persona=None)
    assert page.html == "<div>hi</div>" and page.final_url.startswith("https://")

def test_capture_page_defaults():
    p = CapturePage(html="<x/>", final_url="https://e.com")
    assert p.screenshots == [] and p.meta == {}
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `capture/base.py` (`CapturePage`, `CaptureClient`), `capture/browser.py`
  (`BrowserSession` with lazy Playwright import), and `tests/capture/fakes.py`
  (`FakeCaptureClient`). Add `playwright` to project deps (async); document `playwright install`
  as a deploy/live prerequisite (not needed for the default suite).
- [ ] **4. Run → pass**; `mypy src/gw_geo/common` unaffected. Confirm importing `capture.browser`
  does **not** import Playwright at module load.
- [ ] **5. Commit:** `feat(capture): CaptureClient seam + BrowserSession + fake capturer`

## Acceptance
- `CaptureClient` is a runtime-checkable Protocol; `FakeCaptureClient` satisfies it and serves
  recorded pages; `BrowserSession` isolates all Playwright use behind a lazy import; default suite
  needs no browser.
