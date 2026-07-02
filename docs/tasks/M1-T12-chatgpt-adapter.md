# M1-T12 — Consumer ChatGPT adapter (DOM parse → ProbeResult)

**Depends on:** T07 (capture seam) · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** Playwright-surface adapter for the **consumer ChatGPT UI** (distinct from the M0 OpenAI
API adapter). It calls `capture.fetch(...)` then parses `CapturePage.html` into `answer_text` +
`cited_urls`. Tested hermetically against a recorded HTML fixture via `FakeCaptureClient`; add a T10
contract-suite entry.

**Files:**
- Create: `src/gw_geo/measurement/probe/chatgpt_ui.py`
- Test: `tests/measurement/probe/test_chatgpt_ui.py`, `tests/fixtures/answers/chatgpt_ui.html`
- Modify: `tests/measurement/probe/test_adapter_contract.py`, `tests/measurement/probe/fixtures.py`
  (add `("chatgpt", ...)` CASES row + `mock_for` branch)

## Interface

```python
from gw_geo.capture.base import CaptureClient

class ChatGPTAdapter:
    name = "chatgpt"; supports_citations = True
    def __init__(self, capture: CaptureClient) -> None: ...
    async def probe(self, prompt: str, *, geo: str = "us",
                    persona: str | None = None) -> ProbeResult: ...
```

> `name = "chatgpt"` is the **consumer UI** surface; the M0 API surface is `name = "openai"`. They
> are two distinct engines and both live in the T10 suite.

`probe` calls `capture.fetch(prompt, surface="chatgpt", geo=geo, persona=persona)` → parses the
assistant message DOM: message text → `answer_text`; inline/footnote citation links (`<a href>`) →
`cited_urls` (normalized, de-duped). `persona` selects the authenticated account (T10); `geo` selects
proxy geo. Resilient parser (tolerate missing nodes; empty citations rather than raise).

## Steps
- [ ] **1. Failing test** `tests/measurement/probe/test_chatgpt_ui.py`:

```python
import pathlib
from gw_geo.capture.base import CapturePage
from gw_geo.measurement.probe.chatgpt_ui import ChatGPTAdapter
from gw_geo.measurement.probe.base import EngineAdapter
from tests.capture.fakes import FakeCaptureClient

HTML = pathlib.Path("tests/fixtures/answers/chatgpt_ui.html").read_text()

async def test_probe_parses_assistant_message_and_citations():
    cap = FakeCaptureClient({"chatgpt":
        CapturePage(html=HTML, final_url="https://chatgpt.com/c/abc")})
    a = ChatGPTAdapter(capture=cap)
    assert isinstance(a, EngineAdapter)
    r = await a.probe("best crm for smb?", persona="smb_buyer")
    assert r.engine == "chatgpt"
    assert r.answer_text
    assert all(u.startswith("http") for u in r.cited_urls)
```

Author `chatgpt_ui.html` as a realistic (sanitized) assistant-turn DOM with message text and source
links.

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `chatgpt_ui.py` (reuse the HTML-parsing dep from T11; resilient selectors;
  normalize + de-dup URLs).
- [ ] **4. Run → pass**; mypy clean; add a garbled-HTML resilience test (no raise, `cited_urls == []`).
- [ ] **5. Add T10 entry:** append `("chatgpt", lambda: ChatGPTAdapter(capture=<fixture-backed FakeCaptureClient>))`
  to `CASES` and a `mock_for("chatgpt")` branch building the `FakeCaptureClient` from the HTML
  fixture. Run the contract suite → pass.
- [ ] **6. Commit:** `feat(measurement): consumer chatgpt UI adapter`

## Acceptance
- Conforms to `EngineAdapter`; `name == "chatgpt"` (distinct from `openai`); parses message text +
  citations from recorded HTML via a fake capturer; resilient; hermetic; passes the T10 contract
  suite.
