# M1-T19 — Contract completeness + GA validation

**Depends on:** T18 (validates all adapters via the T10 suite) · **Wave:** 3
**Suggested agent:** general-purpose (final integration/validation task)

**Goal:** Prove Measurement GA: assert the T10 contract suite covers **all ≥8 M1 engines**, and add
a hermetic end-to-end runner test that measures across a representative multi-engine set (API +
Playwright) and persists snapshots — closing M1's definition of done (m1-design §1, §8).

**Files:**
- Create: `tests/measurement/probe/test_contract_completeness.py`
- Create: `tests/test_ga_runner.py`
- Modify: `docs/tasks/M1-README.md` (tick off "≥8 engines in T10 suite" if a checklist is present)

## What it validates
- The T10 `CASES` list contains **every** M1 engine: `perplexity`, `openai`, `gemini`, `claude`,
  `copilot`, `deepseek`, `google_ai_overviews`, `chatgpt`, `grok` (9 total ≥ the 8-engine GA bar).
- Each name is unique; each factory yields an object that `isinstance(..., EngineAdapter)`.
- A hermetic `run_measurement` pass over a mixed engine set (one API + one Playwright adapter,
  fixture/fake-backed) produces one `VisibilitySnapshot` per `(engine, geo, persona)` and persists
  rows — no live API/browser/AWS.

## Steps
- [ ] **1. Failing test** `tests/measurement/probe/test_contract_completeness.py`:

```python
from tests.measurement.probe.test_adapter_contract import CASES

REQUIRED = {"perplexity", "openai", "gemini", "claude", "copilot", "deepseek",
            "google_ai_overviews", "chatgpt", "grok"}

def test_all_m1_engines_present_in_contract_suite():
    names = [name for name, _ in CASES]
    assert REQUIRED <= set(names)              # ≥8-engine GA bar met
    assert len(names) == len(set(names))       # unique
    assert len(REQUIRED) >= 8
```

- [ ] **2. Failing GA runner test** `tests/test_ga_runner.py` (register a fixture-backed API adapter
  + a fake-capturer Playwright adapter; SQLite; in-memory archive; stub extractor):

```python
from gw_geo.measurement.probe import base
from gw_geo.measurement.runner import run_measurement
# ... register two adapters (e.g. a fixture-backed GeminiAdapter via respx, and an
#     AIOverviewsAdapter over a FakeCaptureClient), seed Tenant+Brand+Prompts, then:
async def test_ga_multi_engine_snapshot(seeded_session):
    snaps = await run_measurement(session=seeded_session, tenant_id="t1", brand_id="b1",
        engines=["gemini", "google_ai_overviews"], geos=["us"], personas=[None],
        n_samples=2, extractor=StubExtractor(), archive=MemArchive(), date="2026-07-02")
    assert {s.engine for s in snaps} == {"gemini", "google_ai_overviews"}
    assert all(s.n_samples > 0 and s.ci_low <= s.ci_high for s in snaps)
```

- [ ] **3. Run → fail** (until all adapters + T18 are merged and their `CASES`/`mock_for` entries
  landed).
- [ ] **4. Implement** any missing glue only if needed (no new engines here — this task *validates*).
  Run the full default suite `pytest -m "not live"` → green; confirm the live fleet path stays
  deselected.
- [ ] **5. Run → pass**; `ruff check` + `mypy src/gw_geo/common` clean.
- [ ] **6. Commit:** `test(measurement): m1 contract completeness + GA runner validation`

## Acceptance
- The T10 suite provably covers ≥8 engines (all 9 M1 engines, unique names, each an `EngineAdapter`);
  a hermetic multi-engine `run_measurement` produces + persists snapshots per (engine, geo, persona);
  full default suite green with the live path deselected — **M1 definition of done met.**
