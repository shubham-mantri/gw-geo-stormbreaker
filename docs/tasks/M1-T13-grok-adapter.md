# M1-T13 — Grok adapter (DOM parse → ProbeResult)

**Depends on:** T07 (capture seam) · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** Playwright-surface adapter for Grok (x.com / grok.com consumer UI). It calls
`capture.fetch(...)` then parses `CapturePage.html` into `answer_text` + `cited_urls`. Tested
hermetically against a recorded HTML fixture via `FakeCaptureClient`; add a T10 contract-suite entry.

**Files:**
- Create: `src/gw_geo/measurement/probe/grok.py`
- Test: `tests/measurement/probe/test_grok.py`, `tests/fixtures/answers/grok.html`
- Modify: `tests/measurement/probe/test_adapter_contract.py`, `tests/measurement/probe/fixtures.py`
  (add `("grok", ...)` CASES row + `mock_for` branch)

## Interface

```python
from gw_geo.capture.base import CaptureClient

class GrokAdapter:
    name = "grok"; supports_citations = True
    def __init__(self, capture: CaptureClient) -> None: ...
    async def probe(self, prompt: str, *, geo: str = "us",
                    persona: str | None = None) -> ProbeResult: ...
```

`probe` calls `capture.fetch(prompt, surface="grok", geo=geo, persona=persona)` → parses the Grok
answer DOM: answer text → `answer_text`; source/citation links (`<a href>`) → `cited_urls`
(normalized, de-duped). `persona` → authenticated account (T10); `geo` → proxy geo. Resilient parser
(tolerate missing nodes; empty citations rather than raise).

## Steps
- [ ] **1. Failing test** `tests/measurement/probe/test_grok.py`:

```python
import pathlib
from gw_geo.capture.base import CapturePage
from gw_geo.measurement.probe.grok import GrokAdapter
from gw_geo.measurement.probe.base import EngineAdapter
from tests.capture.fakes import FakeCaptureClient

HTML = pathlib.Path("tests/fixtures/answers/grok.html").read_text()

async def test_probe_parses_grok_answer_and_sources():
    cap = FakeCaptureClient({"grok":
        CapturePage(html=HTML, final_url="https://grok.com/chat/abc")})
    a = GrokAdapter(capture=cap)
    assert isinstance(a, EngineAdapter)
    r = await a.probe("best crm for smb?")
    assert r.engine == "grok"
    assert r.answer_text
    assert all(u.startswith("http") for u in r.cited_urls)
```

Author `grok.html` as a realistic (sanitized) Grok answer DOM with answer text and source links.

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `grok.py` (reuse the HTML-parsing dep from T11; resilient selectors;
  normalize + de-dup URLs).
- [ ] **4. Run → pass**; mypy clean; add a garbled-HTML resilience test (no raise, `cited_urls == []`).
- [ ] **5. Add T10 entry:** append `("grok", lambda: GrokAdapter(capture=<fixture-backed FakeCaptureClient>))`
  to `CASES` and a `mock_for("grok")` branch building the `FakeCaptureClient` from the HTML fixture.
  Run the contract suite → pass.
- [ ] **6. Commit:** `feat(measurement): grok adapter`

## Acceptance
- Conforms to `EngineAdapter`; parses answer text + citations from recorded HTML via a fake
  capturer; resilient; hermetic (no live browser); passes the T10 contract suite.
