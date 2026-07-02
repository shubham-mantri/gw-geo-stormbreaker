# M1-T11 — Google AI Overviews adapter (DOM parse → ProbeResult)

**Depends on:** T07 (capture seam) · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** Playwright-surface adapter for Google AI Overviews. It calls `capture.fetch(...)` then
parses `CapturePage.html` (DOM) into `answer_text` + `cited_urls`. Tested hermetically against a
recorded HTML fixture via `FakeCaptureClient`; add a T10 contract-suite entry.

**Files:**
- Create: `src/gw_geo/measurement/probe/ai_overviews.py`
- Test: `tests/measurement/probe/test_ai_overviews.py`,
  `tests/fixtures/answers/google_ai_overviews.html`
- Modify: `tests/measurement/probe/test_adapter_contract.py`, `tests/measurement/probe/fixtures.py`
  (add `("google_ai_overviews", ...)` CASES row + `mock_for` branch)

## Interface

```python
from gw_geo.capture.base import CaptureClient

class AIOverviewsAdapter:
    name = "google_ai_overviews"; supports_citations = True
    def __init__(self, capture: CaptureClient) -> None: ...
    async def probe(self, prompt: str, *, geo: str = "us",
                    persona: str | None = None) -> ProbeResult: ...
```

`probe` calls `capture.fetch(prompt, surface="google_ai_overviews", geo=geo, persona=persona)` →
parses the returned `CapturePage.html` with a resilient DOM parser (`selectolax` or
`beautifulsoup4`): extract the AI Overview answer block text → `answer_text`, and the overview's
source/citation links (`<a href>`) → `cited_urls` (normalized, de-duped). `geo`/`persona` flow to
the capturer (proxy geo / account persona). Parser must tolerate missing/renamed nodes (m1-design
§10 — consumer DOM is unstable): return empty citations rather than raise.

## Steps
- [ ] **1. Failing test** `tests/measurement/probe/test_ai_overviews.py` (fake capturer + HTML fixture):

```python
import pathlib
from gw_geo.capture.base import CapturePage
from gw_geo.measurement.probe.ai_overviews import AIOverviewsAdapter
from gw_geo.measurement.probe.base import EngineAdapter
from tests.capture.fakes import FakeCaptureClient

HTML = pathlib.Path("tests/fixtures/answers/google_ai_overviews.html").read_text()

async def test_probe_parses_overview_and_sources():
    cap = FakeCaptureClient({"google_ai_overviews":
        CapturePage(html=HTML, final_url="https://www.google.com/search?q=best+crm")})
    a = AIOverviewsAdapter(capture=cap)
    assert isinstance(a, EngineAdapter)
    r = await a.probe("best crm for smb?")
    assert r.engine == "google_ai_overviews"
    assert r.answer_text
    assert r.cited_urls and all(u.startswith("http") for u in r.cited_urls)
```

Author `google_ai_overviews.html` as a realistic (sanitized) AI Overview DOM: an answer container
plus a source list with `<a href="https://...">` links.

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `ai_overviews.py` (DOM parser; resilient selectors; normalize + de-dup URLs).
  Add the HTML-parsing dep (`selectolax` or `beautifulsoup4`) to project deps.
- [ ] **4. Run → pass**; mypy clean; add a test that a garbled/empty HTML yields `answer_text` best-effort
  and `cited_urls == []` without raising.
- [ ] **5. Add T10 entry:** append `("google_ai_overviews", lambda: AIOverviewsAdapter(capture=<fixture-backed FakeCaptureClient>))`
  to `CASES` and a `mock_for("google_ai_overviews")` branch that builds the `FakeCaptureClient` from
  the HTML fixture. Run the contract suite → pass.
- [ ] **6. Commit:** `feat(measurement): google ai overviews adapter`

## Acceptance
- Conforms to `EngineAdapter`; parses overview text + source citations from recorded HTML via a fake
  capturer; resilient to DOM changes (no raise on missing nodes); hermetic (no live browser); passes
  the T10 contract suite.
