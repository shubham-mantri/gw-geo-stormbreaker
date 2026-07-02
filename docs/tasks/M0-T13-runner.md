# M0-T13 — Runner (end-to-end measurement orchestration)

**Depends on:** T04, T05, T06, T07, T12, and ≥1 adapter (T08|T09) · **Wave:** 3
**Suggested agent:** general-purpose (integration task — assign after Wave 2 merges)

**Goal:** Wire the pipeline: load prompts → for each (prompt, engine, geo, persona) probe
`n_samples`× under the cost governor → parse → aggregate → persist snapshots + citations
(TRD §5.5). Bounded concurrency; raw payloads archived (S3 in deploy, injected store in tests).

**Files:**
- Create: `src/gw_geo/measurement/runner.py`
- Test: `tests/measurement/test_runner.py`

## Interface

```python
from gw_geo.common.models import VisibilitySnapshot

class RawArchive(Protocol):
    def put(self, key: str, payload: dict) -> str: ...   # returns storage key/ref

async def run_measurement(
    *, session, tenant_id: str, brand_id: str,
    engines: list[str], geos: list[str], personas: list[str | None],
    n_samples: int, extractor, archive, date: str,
    max_concurrency: int = 8,
) -> list[VisibilitySnapshot]: ...
```

Behavior: resolve adapters via `probe.base.get_adapter`; before each probe call
`CostGovernor.check(estimated_cost)` and skip (flagged) engines that can't be afforded; persist a
`ProbeRun` (with `raw_answer_s3_key` from `archive.put`) + `AnswerExtraction` + `Citation`
upserts; group extractions by (engine, geo, persona) → `aggregate` → persist `VisibilitySnapshot`.
Use `asyncio.Semaphore(max_concurrency)`.

## Steps
- [ ] **1. Failing test** `tests/measurement/test_runner.py` (register fake in-memory adapters,
  SQLite session, in-memory archive, stub extractor):

```python
import pytest
from gw_geo.common.models import ProbeResult, Brand, Prompt
from gw_geo.measurement.probe import base
from gw_geo.measurement.runner import run_measurement

class FakeAdapter:
    name="fake"; supports_citations=True
    async def probe(self, prompt, *, geo="us", persona=None):
        return ProbeResult(engine="fake", answer_text="Foo is best",
                           cited_urls=["https://foo.com"], cost_usd=0.001)

class StubExtractor:
    def extract(self, text, brand):
        return {"brand_mentioned": True, "position": 1, "sentiment": "positive",
                "competitors_present": []}

class MemArchive:
    def __init__(self): self.store={}
    def put(self, key, payload): self.store[key]=payload; return key

async def test_runner_produces_snapshot(seeded_session):
    # seeded_session: Tenant(budget=1.0) + Brand(b1) + 3 Prompts for b1
    base.clear_registry(); base.register(FakeAdapter())
    snaps = await run_measurement(session=seeded_session, tenant_id="t1", brand_id="b1",
        engines=["fake"], geos=["us"], personas=[None], n_samples=4,
        extractor=StubExtractor(), archive=MemArchive(), date="2026-07-02")
    assert len(snaps) == 1
    s = snaps[0]
    assert s.engine=="fake" and s.n_samples == 3*4 and s.mention_rate == 1.0
    assert s.ci_low <= 1.0 <= s.ci_high
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `run_measurement`. Respect the cost governor; persist ProbeRun/Extraction/
  Citation/Snapshot via the T04 session; archive raw via injected `archive`.
- [ ] **4. Run → pass**; add a test asserting a `BudgetExceeded`-tight budget yields a partial/empty
  result rather than crashing.
- [ ] **5. Commit:** `feat(measurement): end-to-end measurement runner`

## Acceptance
- Produces one snapshot per (engine, geo, persona) with `n_samples == n_prompts * n_samples`;
  persists rows; honors the cost budget; hermetic (no live API/AWS).
