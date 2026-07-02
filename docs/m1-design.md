# M1 Design — Measurement GA

**Design spec for Milestone 1** · **Status:** Draft v1 · **Owner:** dev@gushwork.ai · **Date:** 2026-07-02
**Companion to:** [`trd.md`](trd.md) (§5.2 engine list, §5.6 drift canary, §13 milestone map, §14 open items)
and [`prd.md`](prd.md). This spec is the input to the M1 task breakdown (`tasks/M1-*.md`).

---

## 1. Goal (definition of done)

**Measurement GA:** the M0 pipeline, extended to **≥8 AI engines**, **geo/persona-aware**,
**drift-monitored**, and exposing a **dashboards feed** — all under the existing cost governor and
multi-tenant scoping, with hermetic CI (no live API/browser/AWS calls in tests, TRD §12).

M1 adds **zero changes to the M0 core contracts** (`EngineAdapter` T06, `ProbeResult`/models T02,
`run_measurement` T13). Every new engine is "one adapter + one contract-suite entry" (architecture
rule: engine-adapter isolation). New subsystems (capture fleet, drift, feed) attach at the edges.

Engines after M1 (9 total): M0's `perplexity`, `openai` + **API:** `gemini`, `claude`,
`copilot` (Bing), `deepseek` + **Playwright surfaces:** `google_ai_overviews`, `chatgpt` (consumer
UI), `grok`.

---

## 2. Subsystem A — API engine adapters

Four new adapters, each a direct analogue of T08/T09 (Perplexity/OpenAI):

| Engine | `name` | Endpoint / client | Notes |
|---|---|---|---|
| Gemini | `gemini` | Google Generative Language API via `httpx` | citations from grounding metadata |
| Claude | `claude` | Anthropic Messages API via `httpx` (web-search tool) | citations from tool results |
| Copilot/Bing | `copilot` | Bing/Copilot API via `httpx` | |
| DeepSeek | `deepseek` | DeepSeek chat API via `httpx` | **config-toggled off by default** (TRD OT3) |

**Pattern (identical for each):**
- Class conforms to `EngineAdapter` (T06): `name`, `supports_citations`, `__init__(api_key, client=None, model=...)`, `async def probe(prompt, *, geo="us", persona=None) -> ProbeResult`.
- HTTP via injected `httpx.AsyncClient` (constructed if `None`); bearer/appropriate auth.
- Map provider payload → `ProbeResult` (answer_text, cited_urls, raw, latency_ms, cost_usd via a per-model rate table).
- Import side-effect-free; registered in `build_runtime` when its key is set.
- **Tests:** `respx`-mocked, one recorded fixture per engine under `tests/fixtures/answers/`, plus a new `(name, factory)` row in the **T10 contract suite** + a `mock_for` branch.

**Client choice:** `httpx` for all four (consistent with M0, cleanly `respx`-mockable) rather than
per-vendor SDKs.

---

## 3. Subsystem B — Capture fleet + Playwright adapters (full in-house)

Consumer surfaces (Google AI Overviews, consumer ChatGPT, Grok) have no citation-returning API, so
they require headless capture (TRD §3, §5.2). M1 builds the **full in-house fleet** (TRD OT1),
behind a DI seam so adapters stay testable.

### 3.1 `capture/` module
```python
# capture/base.py
class CapturePage(BaseModel):
    html: str
    final_url: str
    screenshots: list[str] = Field(default_factory=list)   # optional S3 refs
    meta: dict[str, Any] = Field(default_factory=dict)

class CaptureClient(Protocol):
    async def fetch(self, query: str, *, surface: str, geo: str,
                    persona: str | None) -> CapturePage: ...
```
- **`ProxyPool`** — geo-aware acquire/release, rotation, health/backoff. `acquire(geo) -> Proxy`.
- **`AccountPool` / session store** — per-(surface, persona) authenticated sessions (cookies/tokens), acquire/release, rotation on ban. Session material from SSM/secret store; never in repo.
- **`BrowserSession`** — async Playwright context wired with a proxy + account cookies + anti-bot/stealth (user-agent, timing, fingerprint) + retry.
- **`LiveCaptureClient`** — composes `ProxyPool` + `AccountPool` + `BrowserSession` to implement `CaptureClient.fetch` (navigate/submit the surface, return `CapturePage`).

### 3.2 Playwright adapters
`AIOverviewsAdapter` (`google_ai_overviews`), `ChatGPTAdapter` (`chatgpt`), `GrokAdapter` (`grok`).
Each: `__init__(capture: CaptureClient, ...)`, conforms to `EngineAdapter`, `probe()` calls
`capture.fetch(...)` then parses `CapturePage.html` (DOM) → `answer_text` + `cited_urls` →
`ProbeResult`.

### 3.3 Testing (how "full fleet" stays hermetic)
- **CI (hermetic):** adapters tested against **recorded HTML/DOM fixtures** via a **fake `CaptureClient`**; `ProxyPool`/`AccountPool` unit-tested with fakes (no live browser/proxy/account). New adapters also join the T10 contract suite (fixture-backed).
- **Out-of-CI (live):** the real `LiveCaptureClient` + fleet is validated by a **separate, marked integration path** (`@pytest.mark.live`, skipped by default; run manually / in a gated job). This path exercises real Playwright/proxies/accounts and is never in the default `pytest`/CI run.

---

## 4. Subsystem C — Drift canary (`orchestration/drift.py`)

Detect when an engine's behavior shifts (TRD §5.6, architecture step 8 "re-learn").

- **Canary set:** a small fixed set of (engine, prompt, brand) with **known-good baselines**
  (expected mention/citation rate), stored via config/seed.
- **Run:** daily (EventBridge cron → Lambda handler), probing the canary set through the normal
  adapters and aggregating current rates.
- **Compare:** `drop = baseline_rate - observed_rate`; if `drop > threshold` (default 0.2) →
  **breach**.
- **On breach:** write a `drift_event` row, emit an alert (structured log + SNS topic in deploy /
  stdout locally), and set a **retrain flag** on the event.

```python
class DriftResult(BaseModel):
    engine: str; canary_id: str
    baseline_rate: float; observed_rate: float; drop: float
    breached: bool
def run_drift_canary(session, *, engines: list[str], threshold: float = 0.2,
                     extractor, archive, date: str) -> list[DriftResult]: ...
```

---

## 5. Subsystem D — Dashboards feed (`measurement/feed.py`)

A read/query layer producing dashboard-ready aggregates from `visibility_snapshot`, consumed by the
M2 dashboard/API repo (`gw-api-geo`). In-repo query module **plus** a `visibility_rollup` table for
efficient time-series.

```python
def visibility_timeseries(session, *, tenant_id, brand_id, engine=None, geo=None,
                          persona=None, since: str, until: str) -> list[dict[str, Any]]: ...
def share_of_voice_trend(session, *, tenant_id, brand_id, since, until) -> list[dict[str, Any]]: ...
def citation_source_mix(session, *, tenant_id, brand_id, since, until) -> dict[str, Any]: ...
```
All queries are **tenant-scoped**. `visibility_rollup` is populated from `visibility_snapshot`
(daily rollup per brand/engine/geo/persona).

---

## 6. Cross-cutting

- **Geo/persona threading:** now meaningful — `geo` → proxy geo selection (Playwright) and API geo
  params where supported; `persona` → account/profile selection (Playwright). API adapters that
  can't target geo/persona document it (as M0's do).
- **Config (`Settings`):** add `gemini_api_key`, `copilot_api_key`, `deepseek_api_key`,
  `deepseek_enabled: bool = False`, proxy-pool + account-pool config refs, `drift_threshold: float = 0.2`,
  `playwright_headless: bool = True`. (`anthropic_api_key` already exists.)
- **Data model additions (Alembic migration):**
  - `drift_event(id, engine, canary_id, baseline_rate, observed_rate, drop, breached, retrain_flag, ts)`
    — **system-level** (engine drift is global; intentional, documented exception to the per-row `tenant_id` rule).
  - `visibility_rollup(id, tenant_id, brand_id, engine, geo, persona, date, mention_rate, citation_rate, avg_position, sentiment_score, share_of_voice, n_samples)`
    — daily rollup of `visibility_snapshot` for fast dashboard time-series (tenant-scoped).
- **Deps:** add `playwright` (async); `playwright install` documented for local/deploy. No per-vendor
  API SDKs (httpx everywhere).
- **Conventions (unchanged from M0):** branch `m1/T<NN>-<slug>`, TDD, hermetic tests, mypy-strict on
  `common/`, `Co-Authored-By: Claude Opus 4.8 (1M context)` trailer, per-task commit, orchestrator
  merges per wave. Everything local (no remote push).

---

## 7. Task DAG & waves (~19 tasks)

| Task | Depends on | Summary |
|---|---|---|
| M1-T01 config & secrets | M0 config | engine keys, proxy/account config, drift threshold, playwright flags |
| M1-T02 migrations | M0 db | `drift_event` + `visibility_rollup` tables (Alembic) |
| M1-T03 Gemini adapter | T06 | API adapter + fixture + T10 entry |
| M1-T04 Claude adapter | T06 | API adapter + fixture + T10 entry |
| M1-T05 Copilot/Bing adapter | T06 | API adapter + fixture + T10 entry |
| M1-T06 DeepSeek adapter | T06 | API adapter (toggle-gated) + fixture + T10 entry |
| M1-T07 CaptureClient seam + BrowserSession | T01 | Playwright abstraction + fake capturer for tests |
| M1-T08 feed query module | M0 db/models | `measurement/feed.py` aggregates (tenant-scoped) |
| M1-T09 ProxyPool | T01 | geo-aware pool + health |
| M1-T10 AccountPool + anti-bot | T01 | per-surface sessions + stealth |
| M1-T11 AI Overviews adapter | T07 | DOM parse → ProbeResult + HTML fixtures + T10 entry |
| M1-T12 consumer ChatGPT adapter | T07 | + HTML fixtures + T10 entry |
| M1-T13 Grok adapter | T07 | + HTML fixtures + T10 entry |
| M1-T14 drift canary | T02, M0 aggregate | `drift.py` + drift_event write + alert hook |
| M1-T15 feed rollup | T08, T02 | populate `visibility_rollup` |
| M1-T16 live CaptureClient (fleet) | T07, T09, T10 | compose proxy+account+browser; out-of-CI live validation |
| M1-T17 drift schedule/handler | T14 | EventBridge cron → Lambda + serverless.yml |
| M1-T18 build_runtime + CLI wiring | T03–T06, T11–T13, T16 | register all engines by config; handlers/serverless |
| M1-T19 contract completeness + GA validation | T18 | all ≥8 engines in T10 suite; hermetic runner GA test |

```
Wave 0 (foundation):   T01  T02
Wave 1 (adapters/core): T03 T04 T05 T06  T07  T08
Wave 2 (fleet/PW/drift): T09 T10  T11 T12 T13  T14  T15
Wave 3 (integration):   T16  T17  T18  →  T19
```
Intra-wave dependency note (as in M0): T16 needs T09+T10 (Wave 2) merged, so it runs early in
Wave 3; T18 needs all adapters + T16; T19 last.

---

## 8. Testing strategy

- Hermetic CI: `respx` for API adapters; **recorded HTML fixtures + fake `CaptureClient`** for
  Playwright adapters; `moto` for AWS; SQLite for DB. No live calls in the default suite.
- **Contract suite is the gate:** every new adapter (API and Playwright) must pass the T10
  `test_adapter_contract` suite — this is what guarantees "≥8 engines, none drifted from contract".
- Live fleet: `@pytest.mark.live` integration tests, deselected by default (`-m "not live"`), run
  manually/gated.
- Property/edge tests continue for aggregation; drift threshold logic unit-tested; feed queries
  tested against seeded SQLite.

---

## 9. Confirmed decisions
1. **DeepSeek** included but `deepseek_enabled=False` by default (TRD OT3).
2. **`httpx`** for all API adapters (no per-vendor SDKs).
3. **Drift** → `drift_event` table + structured-log/SNS alert + retrain flag.
4. **Dashboards feed** → in-repo query module **+** `visibility_rollup` table.
5. **Final artifact** → `docs/tasks/M1-T*.md` + an `M1` `tasks/README` (same format as M0).

## 10. Open items / risks
- Consumer-surface DOM is unstable → parsers must be resilient; drift canary partly guards this.
- Capture fleet (proxies/accounts) is anti-bot-sensitive and the highest external-risk piece; live
  validation is out-of-CI and may need iteration. White-hat only (no cloaking/injection; PRD NG1).
- Provider API shapes (Gemini grounding, Claude web-search tool, Copilot, DeepSeek) must be verified
  against current docs when each adapter is built.
- Real proxy/account credentials + `playwright install` are deploy/runtime prerequisites (out of CI).
