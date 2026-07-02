# M1-T18 — build_runtime + CLI/handler wiring (register all engines)

**Depends on:** T03–T06 (API adapters), T11–T13 (Playwright adapters), T16 (live fleet)
**Wave:** 3 · **Suggested agent:** general-purpose (integration task — assign after adapters + T16 merge)

**Goal:** Extend `build_runtime` (M0-T14 `common/wiring.py`) to register every M1 engine by config:
the four API adapters when their keys are set (DeepSeek also gated on `deepseek_enabled`), and the
three Playwright adapters wired to a `CaptureClient` (the `LiveCaptureClient` fleet in deploy; a
capturer injected in tests). Extend the CLI to accept the new engine names.

**Files:**
- Modify: `src/gw_geo/common/wiring.py`, `src/gw_geo/cli.py`
- Modify: `serverless.yml` (env vars for new keys / capture-fleet refs)
- Test: `tests/test_wiring.py`, `tests/test_cli.py` (extend)

## Interface

```python
# common/wiring.py — build_runtime(settings) -> dict, now registering M1 engines
def build_runtime(settings, *, capture=None) -> dict: ...
# registers, keyed by config:
#   perplexity, openai (M0) + gemini/claude/copilot (if key set) + deepseek (if key AND deepseek_enabled)
#   + google_ai_overviews/chatgpt/grok (Playwright, wired to `capture` — LiveCaptureClient in deploy)
# returns {"extractor":..., "archive":..., "engines":[registered names]}
```

Registration must be **idempotent-safe** for tests (call `probe.base.clear_registry()` at the start
of `build_runtime`, then register). Playwright adapters receive the injected `capture` (a
`FakeCaptureClient` in tests; `LiveCaptureClient` built from `ProxyPool`/`AccountPool` in deploy).

## Steps
- [ ] **1. Failing test** — add to `tests/test_wiring.py`:

```python
from gw_geo.common.config import Settings
from gw_geo.common.wiring import build_runtime
from tests.capture.fakes import FakeCaptureClient
from gw_geo.capture.base import CapturePage

def _capture():
    page = CapturePage(html="<div>x</div>", final_url="https://e.com")
    return FakeCaptureClient({s: page for s in ("google_ai_overviews", "chatgpt", "grok")})

def test_registers_api_engines_by_key():
    s = Settings(gemini_api_key="g", copilot_api_key="c", anthropic_api_key="a",
                 perplexity_api_key="p", openai_api_key="o")   # deepseek key empty
    rt = build_runtime(s, capture=_capture())
    assert {"perplexity","openai","gemini","claude","copilot"} <= set(rt["engines"])
    assert "deepseek" not in rt["engines"]        # no key

def test_deepseek_gated_on_toggle():
    s = Settings(deepseek_api_key="d", deepseek_enabled=False)
    assert "deepseek" not in build_runtime(s, capture=_capture())["engines"]
    s2 = Settings(deepseek_api_key="d", deepseek_enabled=True)
    assert "deepseek" in build_runtime(s2, capture=_capture())["engines"]

def test_registers_playwright_engines_with_capture():
    rt = build_runtime(Settings(), capture=_capture())
    assert {"google_ai_overviews","chatgpt","grok"} <= set(rt["engines"])
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** the `build_runtime` extension (conditional registration per key/toggle;
  Playwright adapters wired to `capture`; build `LiveCaptureClient` from pools when `capture is None`
  and fleet config refs are set). Extend `cli.py` so `--engines` accepts the new names and passes
  `capture` through.
- [ ] **4. Run → pass**; extend `tests/test_cli.py` to assert a new engine (e.g. `gemini`) parses and
  reaches `run_measurement`; mypy clean on `common/wiring.py`.
- [ ] **5. Add** new keys / capture-fleet config refs to `serverless.yml` env.
- [ ] **6. Commit:** `feat(wiring): register all m1 engines by config + cli wiring`

## Acceptance
- `build_runtime` registers each API engine when its key is set (DeepSeek also requires
  `deepseek_enabled`) and all three Playwright engines wired to a `CaptureClient`; CLI accepts the
  new engine names; registration is test-safe; hermetic.
