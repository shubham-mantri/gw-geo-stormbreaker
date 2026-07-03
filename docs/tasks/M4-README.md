# M4 Implementation Tasks — gw-geo-stormbreaker

**Status:** ✅ T01–T17 complete — M4 done-gate green (`tests/test_m4_e2e.py`): the closed loop
holds end-to-end (discovery → compliance → workflow → `placed`), the white-hat compliance gate
(PRD NG1) blocks astroturf/no-disclosure placements on every channel and `mark_placed` is
unbypassable, a breached+flagged drift event drives a retrain job, and `billing_summary` composes
metered usage with a RaaS charge — all with zero live network/AWS/posting.

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` to
> implement these task-by-task. Each task file is a self-contained unit of work for one subagent,
> written TDD-first. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal (M4):** close the loop — **off-site seeding** (place content on the sources LLMs cite,
gated by a hard white-hat compliance engine), **self-adaptation** (drift-breach → retrain,
bandit effort allocation, continuous loop), and **RaaS pricing/billing** (usage + attributed
results). See [`../m4-design.md`](../m4-design.md) for the full design and
[`../prd.md`](../prd.md) §6.5/§6.6/§9 (definition of done).

**Read before starting any task:** [`../m4-design.md`](../m4-design.md),
[`../prd.md`](../prd.md) (§6.5 seeding, §6.6 orchestration, §9 pricing, **NG1 white-hat**),
[`../trd.md`](../trd.md) (§10 seeding, §6.3/§8 bandit, §5.6 drift), [`../m1-design.md`](../m1-design.md)
§4 (drift canary), [`../ui-spec.md`](../ui-spec.md) (§3.5 seeding tracker, §3.7 alerts, §7 M4 map).
Honor the interface contracts in the design spec exactly — parallel agents depend on them.

---

## Execution model (for the orchestrator)

Dispatch by **wave**. Within a wave, tasks are independent → run subagents **in parallel**.
Between waves, the orchestrator reviews merged output (tests green, interfaces match the design
spec, **compliance gate enforced**) before starting the next wave. Every task ends with its own commit.

```
Wave 0 (foundation, no deps):      T01  T02
Wave 1 (primitives):               T03  T04  T05  T06  T07  T08  T09
Wave 2 (compose):                  T10  T11  T12  T13  T14
Wave 3 (integration):              T15  T16  →  T17
```

### Dependency DAG
| Task | Depends on |
|---|---|
| T01 config & secrets | M0 config |
| T02 migrations | M0 db, M1 `drift_event` |
| T03 compliance rules engine (hard gate) | M0 models |
| T04 channel catalog + rule seed | T02, T03 |
| T05 target discovery | M0 models (injected `SourceMap`) |
| T06 per-channel briefs | T03, T05 |
| T07 bandit optimizer | M0 models |
| T08 usage metering | T02 |
| T09 RaaS pricing / invoice | M0 models (injected `AttributionSource`) |
| T10 seeding workflow (compliance-gated) | T02, T03, T04 |
| T11 corroboration tracking | T02, T05 |
| T12 retrain trigger | T02, M1 `drift_event` |
| T13 billing views | T08, T09 |
| T14 effort allocation service | T07, T10 |
| T15 adaptation-cycle scheduler | T05, T10, T12, T14 |
| T16 handlers + serverless wiring | T13, T15 |
| T17 M4 validation (gate + loop) | T01–T16 |

---

## Conventions (all tasks)
- **TDD:** write the failing test first, watch it fail, implement minimally, watch it pass, commit.
- **Hermetic tests:** no live API/AWS calls **and no live posting**. Inject every external
  collaborator (`SourceMap`, `BriefLLM`, `Retrainer`, `AttributionSource`) via constructor/arg; use
  fakes. Mock AWS with `moto` only where a handler is exercised. SQLite for DB.
- **White-hat is a tested gate (PRD NG1):** the compliance engine must *block* astroturf / hidden-text
  / cloaking / missing-disclosure, and the workflow must be unbypassable. This is an acceptance gate.
- **Decoupled:** no imports of M2/M3 concrete code and no Gushwork/shared-service/cross-repo deps.
- **Types:** `common/` is mypy-strict. Match model/field/method names to `m4-design.md` exactly.
- **Commit message:** `feat(<area>): <what>` or `test(<area>): <what>`; end with the
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer.
- **Branching:** each task on its own branch `m4/T<NN>-<slug>`; orchestrator merges after review.
- **Definition of done (per task):** listed acceptance criteria met, `pytest` green,
  `ruff check` + `mypy src/gw_geo/common` clean, committed.

## Task files
- [`M4-T01-config.md`](M4-T01-config.md)
- [`M4-T02-migrations.md`](M4-T02-migrations.md)
- [`M4-T03-compliance-engine.md`](M4-T03-compliance-engine.md)
- [`M4-T04-channel-catalog.md`](M4-T04-channel-catalog.md)
- [`M4-T05-target-discovery.md`](M4-T05-target-discovery.md)
- [`M4-T06-briefs.md`](M4-T06-briefs.md)
- [`M4-T07-bandit.md`](M4-T07-bandit.md)
- [`M4-T08-usage-metering.md`](M4-T08-usage-metering.md)
- [`M4-T09-pricing-invoice.md`](M4-T09-pricing-invoice.md)
- [`M4-T10-seeding-workflow.md`](M4-T10-seeding-workflow.md)
- [`M4-T11-corroboration.md`](M4-T11-corroboration.md)
- [`M4-T12-retrain-trigger.md`](M4-T12-retrain-trigger.md)
- [`M4-T13-billing-views.md`](M4-T13-billing-views.md)
- [`M4-T14-effort-allocation.md`](M4-T14-effort-allocation.md)
- [`M4-T15-scheduler.md`](M4-T15-scheduler.md)
- [`M4-T16-handlers-serverless.md`](M4-T16-handlers-serverless.md)
- [`M4-T17-m4-validation.md`](M4-T17-m4-validation.md)
