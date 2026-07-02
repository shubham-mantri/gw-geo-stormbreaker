# M1-T04 — Claude adapter (Anthropic Messages API + web-search tool)

**Depends on:** M0-T06 (engine adapter base) · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** API adapter for Anthropic Claude via the Messages API over `httpx`, with the server-side
`web_search` tool enabled so answers reflect live retrieval; extract citations from the tool
results. HTTP mocked in tests (`respx`); one recorded fixture; add a T10 contract-suite entry.

> Note: Claude is *also* used as the M0 parse extractor. This is a distinct **probe** adapter (a
> measured engine surface), separate from the extractor. `anthropic_api_key` already exists in
> `Settings`.

**Files:**
- Create: `src/gw_geo/measurement/probe/claude.py`
- Test: `tests/measurement/probe/test_claude.py`, `tests/fixtures/answers/claude_api.json`
- Modify: `tests/measurement/probe/test_adapter_contract.py`, `tests/measurement/probe/fixtures.py`
  (add `("claude", ...)` CASES row + `mock_for` branch)

## Interface

```python
class ClaudeAdapter:
    name = "claude"; supports_citations = True
    def __init__(self, api_key: str, client: "httpx.AsyncClient | None" = None,
                 model: str = "claude-sonnet-4-5") -> None: ...
    async def probe(self, prompt: str, *, geo: str = "us",
                    persona: str | None = None) -> ProbeResult: ...
```

Endpoint: `POST https://api.anthropic.com/v1/messages` with headers `x-api-key`,
`anthropic-version: 2023-06-01`, and `tools=[{"type": "web_search_20250305", "name": "web_search"}]`.
Map response: concatenate `content[*]` where `type == "text"` → `answer_text`; pull citation URLs
from the `text` blocks' `citations[*].url` and/or `web_search_tool_result` content `url` fields →
`cited_urls` (de-duplicated, order-preserving); measure `latency_ms`; compute `cost_usd` from
`usage.input_tokens`/`output_tokens` via a per-model rate table. Import side-effect-free; registered
in `build_runtime` when `anthropic_api_key` is set (T18).

## Steps
- [ ] **1. Failing test** `tests/measurement/probe/test_claude.py` (mock with `respx`, load fixture):

```python
import httpx, respx, json, pathlib
from gw_geo.measurement.probe.claude import ClaudeAdapter

FIX = json.loads(pathlib.Path("tests/fixtures/answers/claude_api.json").read_text())

@respx.mock
async def test_probe_extracts_web_search_citations():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json=FIX))
    a = ClaudeAdapter(api_key="k", client=httpx.AsyncClient())
    r = await a.probe("best crm for smb?")
    assert r.engine == "claude"
    assert r.answer_text
    assert r.cited_urls and all(u.startswith("http") for u in r.cited_urls)
    assert r.cost_usd > 0
```

The fixture must be a realistic Messages payload with `content` text blocks carrying `citations`
(each with a `url`) and/or a `web_search_tool_result` block, plus a `usage` object.

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `claude.py` per interface (inject `httpx.AsyncClient`, construct if `None`;
  rate table constant in module; de-dup citation URLs preserving order).
- [ ] **4. Run → pass**; mypy clean; confirm `isinstance(adapter, EngineAdapter)`.
- [ ] **5. Add T10 entry:** append `("claude", lambda: ClaudeAdapter(api_key="k", client=httpx.AsyncClient()))`
  to `CASES` and a `mock_for("claude")` branch routing `POST /v1/messages` to `claude_api.json`.
  Run the contract suite → pass.
- [ ] **6. Commit:** `feat(measurement): claude adapter`

## Acceptance
- Conforms to `EngineAdapter`; extracts answer text + web-search citations + cost; hermetic tests;
  passes the T10 contract suite; does not disturb the M0 Claude extractor.
