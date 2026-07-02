# M1 Implementation Tasks — gw-geo-stormbreaker

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` to
> implement these task-by-task. Each task file is a self-contained unit of work for one subagent,
> written TDD-first. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal (M1 — Measurement GA):** extend the M0 pipeline to **≥8 AI engines**, **geo/persona-aware**,
**drift-monitored**, with a **dashboards feed** — all under the existing cost governor and
multi-tenant scoping, with hermetic CI (no live API/browser/AWS calls in the default suite). M1 adds
**zero changes to the M0 core contracts** (`EngineAdapter` T06, `ProbeResult`/models T02,
`run_measurement` T13): every new engine is "one adapter + one contract-suite entry". See
[`../m1-design.md`](../m1-design.md) for the full design and [`../trd.md`](../trd.md) §5.2 for the
binding `EngineAdapter` contract.

**Read before starting any task:** [`../m1-design.md`](../m1-design.md) (the source of truth) and
[`../trd.md`](../trd.md) (§3–§5, §7, §12). Honor the interface contracts in the TRD exactly —
parallel agents depend on them.

---

## Execution model (for the orchestrator)

Dispatch by **wave**. Within a wave, tasks are independent → run subagents **in parallel**.
Between waves, the orchestrator reviews merged output (tests green, interfaces match the TRD /
m1-design) before starting the next wave. Every task ends with its own commit.

```
Wave 0 (foundation):     T01  T02
Wave 1 (adapters/core):  T03 T04 T05 T06   T07   T08
Wave 2 (fleet/PW/drift): T09 T10   T11 T12 T13   T14   T15
Wave 3 (integration):    T16   T17   T18   →   T19
```

Intra-wave dependency note (as in M0): T16 needs T09+T10 (Wave 2) merged, so it runs early in
Wave 3; T18 needs all adapters + T16; T19 last.

### Dependency DAG
| Task | Depends on |
|---|---|
| T01 config & secrets | M0 config (M0-T03) |
| T02 migrations | M0 db (M0-T04) |
| T03 Gemini adapter | M0-T06 |
| T04 Claude adapter | M0-T06 |
| T05 Copilot/Bing adapter | M0-T06 |
| T06 DeepSeek adapter | M0-T06 |
| T07 CaptureClient seam + BrowserSession | T01 |
| T08 feed query module | M0-T04, M0-T02 |
| T09 ProxyPool | T01 |
| T10 AccountPool + anti-bot | T01 |
| T11 AI Overviews adapter | T07 |
| T12 consumer ChatGPT adapter | T07 |
| T13 Grok adapter | T07 |
| T14 drift canary | T02, M0-T12 (aggregate) |
| T15 feed rollup | T08, T02 |
| T16 live CaptureClient (fleet) | T07, T09, T10 |
| T17 drift schedule/handler | T14 |
| T18 build_runtime + CLI wiring | T03–T06, T11–T13, T16 |
| T19 contract completeness + GA validation | T18 (validates all adapters via T10 suite) |

---

## Conventions (all tasks)

Unchanged from M0 — see [`README.md`](README.md) → "Conventions". In brief:

- **TDD:** write the failing test first, watch it fail, implement minimally, watch it pass, commit.
- **Hermetic tests:** no live API/browser/AWS calls in the default suite. Inject clients via
  constructor; mock HTTP with `respx`, AWS with `moto`, DB on SQLite. Playwright adapters test
  against **recorded HTML/DOM fixtures via a fake `CaptureClient`**; the real fleet
  (`LiveCaptureClient` + proxies/accounts/Playwright) is exercised only behind `@pytest.mark.live`,
  deselected by default (`-m "not live"`).
- **Contract suite is the gate:** every new adapter (API and Playwright) adds a `(name, factory)`
  row to the T10 suite (`tests/measurement/probe/test_adapter_contract.py`) + a `mock_for` branch
  (`tests/measurement/probe/fixtures.py`). This guarantees "≥8 engines, none drifted from contract".
- **Types:** `common/` is mypy-strict. Match model/field/method names to the TRD / m1-design exactly.
- **Commit message:** `feat(<area>): <what>` or `test(<area>): <what>`; end with the
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer.
- **Branching:** each task on its own branch `m1/T<NN>-<slug>`; orchestrator merges after review.
- **Self-contained:** no dependencies on external/shared services or sibling repos; this project
  stands alone. White-hat only (PRD NG1) — no cloaking/injection/astroturf.
- **Definition of done (per task):** listed acceptance criteria met, `pytest -m "not live"` green,
  `ruff check` + `mypy src/gw_geo/common` clean, committed.

## Task files
- [`M1-T01-config-secrets.md`](M1-T01-config-secrets.md)
- [`M1-T02-migrations.md`](M1-T02-migrations.md)
- [`M1-T03-gemini-adapter.md`](M1-T03-gemini-adapter.md)
- [`M1-T04-claude-adapter.md`](M1-T04-claude-adapter.md)
- [`M1-T05-copilot-adapter.md`](M1-T05-copilot-adapter.md)
- [`M1-T06-deepseek-adapter.md`](M1-T06-deepseek-adapter.md)
- [`M1-T07-capture-seam.md`](M1-T07-capture-seam.md)
- [`M1-T08-feed-queries.md`](M1-T08-feed-queries.md)
- [`M1-T09-proxy-pool.md`](M1-T09-proxy-pool.md)
- [`M1-T10-account-pool.md`](M1-T10-account-pool.md)
- [`M1-T11-ai-overviews-adapter.md`](M1-T11-ai-overviews-adapter.md)
- [`M1-T12-chatgpt-adapter.md`](M1-T12-chatgpt-adapter.md)
- [`M1-T13-grok-adapter.md`](M1-T13-grok-adapter.md)
- [`M1-T14-drift-canary.md`](M1-T14-drift-canary.md)
- [`M1-T15-feed-rollup.md`](M1-T15-feed-rollup.md)
- [`M1-T16-live-capture-fleet.md`](M1-T16-live-capture-fleet.md)
- [`M1-T17-drift-schedule-handler.md`](M1-T17-drift-schedule-handler.md)
- [`M1-T18-build-runtime-wiring.md`](M1-T18-build-runtime-wiring.md)
- [`M1-T19-contract-completeness-ga.md`](M1-T19-contract-completeness-ga.md)
