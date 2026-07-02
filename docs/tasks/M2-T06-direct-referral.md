# M2-T06 — Attribution mechanism 1: direct referral capture

**Depends on:** T05 · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** The strongest attribution mechanism (TRD §6, PRD §6.2 #1): classify inbound sessions that
arrived **from an AI engine** by referrer/UTM and write `attribution_link(method="direct")`. A
versioned engine-referrer map; pure classifier + a tenant-scoped linker.

**Files:**
- Create: `src/gw_geo/attribution/referral.py`
- Test: `tests/attribution/test_referral.py`

## Interface

```python
AI_ENGINE_REFERRERS: dict[str, str] = {
    "chatgpt.com": "chatgpt", "chat.openai.com": "chatgpt",
    "perplexity.ai": "perplexity", "www.perplexity.ai": "perplexity",
    "gemini.google.com": "gemini", "copilot.microsoft.com": "copilot",
    "claude.ai": "claude", "grok.com": "grok", "x.com": "grok",
}
def classify_referrer(referrer: str | None, utm: dict[str, str]) -> str | None: ...
    # host match on referrer; fallback to utm_source in the engine set; else None
def link_direct(session, *, tenant_id: str, brand_id: str,
                since: str, until: str) -> list[AttributionLink]: ...
    # for each session in range with an AI referrer, upsert an attribution_link
    # (method="direct", confidence="high", engine=<detected>, value_usd from linked lead if any)
```

## Steps
- [ ] **1. Failing test** `tests/attribution/test_referral.py`:

```python
from gw_geo.attribution.referral import classify_referrer, link_direct

def test_classify_by_host():
    assert classify_referrer("https://chatgpt.com/c/abc", {}) == "chatgpt"
    assert classify_referrer("https://www.perplexity.ai/search", {}) == "perplexity"

def test_classify_by_utm_fallback():
    assert classify_referrer(None, {"utm_source": "gemini"}) == "gemini"

def test_non_ai_referrer_is_none():
    assert classify_referrer("https://google.com/search?q=x", {}) is None

def test_link_direct_creates_links(seeded_sessions):
    # seeded_sessions: 2 sessions for t1/b1 — one from chatgpt.com, one from google.com
    links = link_direct(seeded_sessions, tenant_id="t1", brand_id="b1",
                        since="2026-06-01", until="2026-07-02")
    assert len(links) == 1
    assert links[0].method == "direct" and links[0].engine == "chatgpt"
    assert links[0].confidence == "high"
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `classify_referrer` (normalize host, strip `www.` handled by explicit keys) and
  `link_direct` (tenant-scoped read of `session`, write `attribution_link`; idempotent upsert keyed on
  session_id+method). Reuse M0 URL normalization where useful.
- [ ] **4. Run → pass**; mypy clean on touched `common`.
- [ ] **5. Commit:** `feat(attribution): direct referral capture (mechanism 1)`

## Acceptance
- Classifier maps known engine hosts + `utm_source` fallback and returns `None` for non-AI referrers;
  `link_direct` writes exactly one `direct`/`high` link per AI-referred session, tenant-scoped,
  idempotent; hermetic.
