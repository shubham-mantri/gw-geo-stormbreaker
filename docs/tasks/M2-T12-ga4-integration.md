# M2-T12 — GA4 integration (referral reconciliation)

**Depends on:** T05 · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** Pull GA4 AI-referral channel sessions/conversions to **corroborate** the pixel (m2-design
§5). GA4 is reconciliation only — the pixel stays system of record. Same `Integration` interface as
T11; HTTP `respx`-mocked.

**Files:**
- Create: `src/gw_geo/attribution/integrations/ga4.py`
- Test: `tests/attribution/integrations/test_ga4.py`,
  `tests/fixtures/ga4/report.json`

## Interface

```python
class GA4Integration:
    kind = "ga4"
    def __init__(self, settings, client: "httpx.AsyncClient | None" = None) -> None: ...
    def connect(self, session, *, tenant_id, config) -> dict: ...     # persist integration row
    async def sync(self, session, *, tenant_id, brand_id) -> int: ... # -> reconciled rows
        # runReport on GA4 Data API; extract sessions where source ∈ AI engines;
        # produce a reconciliation record (pixel count vs GA4 count) per engine; return #engines seen

def reconcile(pixel_counts: dict[str, int], ga4_counts: dict[str, int]) -> dict[str, dict]: ...
    # -> {engine: {"pixel": n, "ga4": m, "delta": m-n}}
```

## Steps
- [ ] **1. Failing test** `tests/attribution/integrations/test_ga4.py`:

```python
import httpx, respx, pytest
from gw_geo.attribution.integrations.ga4 import GA4Integration, reconcile

def test_reconcile_computes_delta():
    out = reconcile({"chatgpt": 10, "perplexity": 5}, {"chatgpt": 12, "perplexity": 5})
    assert out["chatgpt"]["delta"] == 2 and out["perplexity"]["delta"] == 0

@respx.mock
async def test_sync_reads_ai_referrals(seeded_session, settings):
    respx.post(url__regex=r"https://analyticsdata\.googleapis\.com/.*:runReport").mock(
        return_value=httpx.Response(200, json={"rows":[
            {"dimensionValues":[{"value":"perplexity.ai"}],"metricValues":[{"value":"7"}]}]}))
    integ = GA4Integration(settings, client=httpx.AsyncClient())
    n = await integ.sync(seeded_session, tenant_id="t1", brand_id="b1")
    assert n >= 1
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** the Data API `runReport` call (creds via `settings.ga4_credentials_ref`,
  injected client), map GA4 source dims → engines (reuse T06 `AI_ENGINE_REFERRERS`), `reconcile`,
  and `connect` persisting an `integration` row. Do NOT overwrite pixel leads (reconciliation only).
- [ ] **4. Run → pass**; mypy clean on touched `common`.
- [ ] **5. Commit:** `feat(attribution): ga4 referral reconciliation integration`

## Acceptance
- `reconcile` computes per-engine pixel-vs-GA4 deltas; `sync` reads AI-referral rows via the Data API
  (respx-mocked) without mutating pixel leads; `connect` persists an `integration` row; hermetic.
