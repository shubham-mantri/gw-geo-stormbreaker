# M0-T12 — Aggregate (extractions → visibility snapshot)

**Depends on:** T02, T07 · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** Roll up N `AnswerExtraction`s for one (brand, engine, geo, persona) into a
`VisibilitySnapshot` with a **Wilson 95% CI** on mention_rate. This is where the non-determinism
rule (TRD §3) is enforced.

**Files:**
- Create: `src/gw_geo/measurement/aggregate.py`
- Test: `tests/measurement/test_aggregate.py`

## Interface

```python
from gw_geo.common.models import AnswerExtraction, VisibilitySnapshot

def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]: ...

def aggregate(extractions: list[AnswerExtraction], *, brand_id: str, engine: str,
              geo: str, persona: str | None, date: str) -> VisibilitySnapshot: ...
```

Computation:
- `mention_rate = mentions/n`; `citation_rate = (# with cited_urls)/n`.
- `ci_low, ci_high = wilson_ci(mentions, n)`.
- `avg_position` = mean of non-null positions (None if never mentioned).
- `sentiment_score` = mean of {positive:+1, neutral:0, comparison:0, negative:-1} over mentions.
- `share_of_voice` = mentions / (mentions + total competitor mentions), 0 if denom 0.
- `n_samples = n`.

## Steps
- [ ] **1. Failing test** `tests/measurement/test_aggregate.py`:

```python
from gw_geo.common.models import AnswerExtraction, Sentiment
from gw_geo.measurement.aggregate import aggregate, wilson_ci

def _ext(m, pos=None, sent=Sentiment.NEUTRAL, cites=(), comps=()):
    return AnswerExtraction(probe_run_id="x", brand_mentioned=m, position=pos,
        sentiment=sent, cited_urls=list(cites), competitors_present=list(comps))

def test_wilson_bounds_in_unit_interval():
    lo, hi = wilson_ci(4, 10); assert 0 <= lo <= 0.4 <= hi <= 1

def test_aggregate_rates_and_ci():
    exts = [_ext(True, 1, Sentiment.POSITIVE, ["https://a"], ["Acme"]),
            _ext(False), _ext(True, 3, Sentiment.NEUTRAL, [], ["Acme","Beta"]),
            _ext(False)]
    s = aggregate(exts, brand_id="b1", engine="perplexity", geo="us",
                  persona=None, date="2026-07-02")
    assert s.n_samples == 4 and s.mention_rate == 0.5 and s.citation_rate == 0.25
    assert s.ci_low < s.mention_rate < s.ci_high
    assert 0 <= s.share_of_voice <= 1 and s.avg_position == 2.0
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `aggregate.py` (Wilson via closed form or `scipy`); mypy-strict.
- [ ] **4. Run → pass.** Optionally add a property test: CI always within [0,1], `ci_low <= rate <= ci_high`.
- [ ] **5. Commit:** `feat(measurement): visibility aggregation with wilson CI`

## Acceptance
- Rates + Wilson CI + SoV + avg_position + sentiment computed correctly; snapshot always carries
  `n_samples`, `ci_low`, `ci_high`.
