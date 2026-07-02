# M2-T08 — Attribution mechanism 3: assisted modeling

**Depends on:** T05 · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** Mechanism 3 (TRD §6, PRD §6.2 #3) — **correlational, always low-confidence**. For buyers
who saw the brand in an AI answer then arrived later via branded search/direct: (a) ingest
self-reported "how did you hear about us"; (b) correlate branded-search lift to visibility gains.
Emits `attribution_link(method="assisted")` flagged `reported` or `modeled` — **never causal**.

**Files:**
- Create: `src/gw_geo/attribution/assisted.py`
- Test: `tests/attribution/test_assisted.py`

## Interface

```python
def assisted_credit(session, *, tenant_id: str, brand_id: str, since: str, until: str,
                    visibility_series: list[dict]) -> list[AttributionLink]: ...
    # self-report: leads with self_reported_source matching an AI engine
    #   -> method="assisted", confidence="reported"
    # branded-lift: correlate branded-search/direct lead volume to visibility_series gains
    #   -> method="assisted", confidence="modeled" (probabilistic weight, NOT a causal claim)

def branded_lift_correlation(visibility_series: list[dict],
                             lead_series: list[dict]) -> float: ...   # Pearson r in [-1, 1]
```
`visibility_series` = `feed.share_of_voice_trend` output (m1-design §5).

## Steps
- [ ] **1. Failing test** `tests/attribution/test_assisted.py`:

```python
from gw_geo.attribution.assisted import assisted_credit, branded_lift_correlation

def test_self_report_creates_reported_link(seeded_self_report):
    # Lead(self_reported_source="ChatGPT", brand b1, tenant t1)
    links = assisted_credit(seeded_self_report, tenant_id="t1", brand_id="b1",
        since="2026-06-01", until="2026-07-02", visibility_series=[])
    assert any(l.method == "assisted" and l.confidence == "reported" for l in links)

def test_branded_lift_correlation_positive():
    vis = [{"date":"2026-06-01","share_of_voice":0.1}, {"date":"2026-06-02","share_of_voice":0.2},
           {"date":"2026-06-03","share_of_voice":0.3}]
    leads = [{"date":"2026-06-01","leads":10}, {"date":"2026-06-02","leads":20},
             {"date":"2026-06-03","leads":30}]
    assert branded_lift_correlation(vis, leads) > 0.9

def test_modeled_link_is_never_high_confidence(seeded_branded):
    links = assisted_credit(seeded_branded, tenant_id="t1", brand_id="b1",
        since="2026-06-01", until="2026-07-02",
        visibility_series=[{"date":"2026-06-01","share_of_voice":0.1}])
    assert all(l.confidence in ("reported", "modeled", "low") for l in links)
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** self-report matching (map `self_reported_source` → engine via the T06 referrer
  map, case-insensitive), `branded_lift_correlation` (Pearson over aligned dates, `scipy.stats`),
  and modeled-credit link creation. Confidence must never exceed `modeled`.
- [ ] **4. Run → pass**; mypy clean on touched `common`.
- [ ] **5. Commit:** `feat(attribution): assisted modeling (mechanism 3)`

## Acceptance
- Self-reports produce `reported` links; branded-lift produces `modeled` links; correlation math
  correct on aligned series; **no assisted link is ever `high` confidence** (honesty rule);
  tenant-scoped; hermetic.
