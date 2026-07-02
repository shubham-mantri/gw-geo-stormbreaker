# M0-T09 — OpenAI (ChatGPT) adapter

**Depends on:** T06 · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** Second engine adapter via the OpenAI Responses API with the web-search tool (so answers
reflect live retrieval + citations, per TRD §3 "monitor consumer-facing behavior, not static
model"). HTTP mocked in tests.

**Files:**
- Create: `src/gw_geo/measurement/probe/openai_chatgpt.py`
- Test: `tests/measurement/probe/test_openai.py`, `tests/fixtures/answers/openai_api.json`

## Interface

```python
class OpenAIAdapter:
    name = "openai"; supports_citations = True
    def __init__(self, api_key: str, client: "httpx.AsyncClient | None" = None,
                 model: str = "gpt-4.1") -> None: ...
    async def probe(self, prompt: str, *, geo: str = "us",
                    persona: str | None = None) -> ProbeResult: ...
```

Use the Responses API with `tools=[{"type":"web_search"}]`. Extract answer text and pull
citation URLs from `url_citation` annotations. Compute `cost_usd` from usage + a rate table.

## Steps
- [ ] **1. Failing test** `tests/measurement/probe/test_openai.py` (mock with `respx`, load fixture):

```python
import httpx, respx, json, pathlib
from gw_geo.measurement.probe.openai_chatgpt import OpenAIAdapter

FIX = json.loads(pathlib.Path("tests/fixtures/answers/openai_api.json").read_text())

@respx.mock
async def test_probe_extracts_text_and_citations():
    respx.post("https://api.openai.com/v1/responses").mock(
        return_value=httpx.Response(200, json=FIX))
    a = OpenAIAdapter(api_key="k", client=httpx.AsyncClient())
    r = await a.probe("best crm for smb?")
    assert r.engine == "openai"
    assert r.answer_text
    assert all(u.startswith("http") for u in r.cited_urls)
```

The fixture must contain a realistic Responses payload with output text + `url_citation`
annotations (author it from the API docs shape).

- [ ] **2. Run → fail.**
- [ ] **3. Implement** the adapter (import side-effect-free; registered at wiring time).
- [ ] **4. Run → pass**; mypy clean; `isinstance(adapter, EngineAdapter)`.
- [ ] **5. Commit:** `feat(measurement): openai chatgpt adapter`

## Acceptance
- Conforms to `EngineAdapter`; extracts answer + citation annotations + cost; hermetic tests;
  passes the T10 contract suite.
