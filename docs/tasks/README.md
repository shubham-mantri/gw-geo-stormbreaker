# M0 Implementation Tasks — gw-geo-stormbreaker

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` to
> implement these task-by-task. Each task file is a self-contained unit of work for one subagent,
> written TDD-first. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal (M0):** a tested, runnable pipeline that turns a brand + seed topics into a persisted
**visibility snapshot** across ≥2 AI engines, with confidence intervals, under a per-tenant cost
budget. See [`../trd.md`](../trd.md) §1 for the definition of done.

**Read before starting any task:** [`../prd.md`](../prd.md) (§6.1), [`../trd.md`](../trd.md)
(§3–§5, §7, §12). Honor the interface contracts in the TRD exactly — parallel agents depend on them.

---

## Execution model (for the orchestrator)

Dispatch by **wave**. Within a wave, tasks are independent → run subagents **in parallel**.
Between waves, the orchestrator reviews merged output (tests green, interfaces match TRD) before
starting the next wave. Every task ends with its own commit.

```
Wave 0 (parallel, no deps):        T01  T02  T03
Wave 1 (needs models/config):      T04  T05  T06  T07
Wave 2 (needs base/models):        T08  T09  T10  T11  T12
Wave 3 (integration):              T13  →  T14
```

### Dependency DAG
| Task | Depends on |
|---|---|
| T01 tooling & CI | — |
| T02 domain models | — |
| T03 config | — |
| T04 db + migrations | T02 |
| T05 cost governor | T02, T04 |
| T06 engine adapter base + registry | T02 |
| T07 parse | T02 |
| T08 Perplexity adapter | T06 |
| T09 OpenAI adapter | T06 |
| T10 adapter contract tests | T06 (validates T08, T09) |
| T11 discover | T02, T03 |
| T12 aggregate | T02, T07 |
| T13 runner (integration) | T04, T05, T06, T07, T12, (T08\|T09) |
| T14 CLI + Lambda handler | T13 |

---

## Conventions (all tasks)
- **TDD:** write the failing test first, watch it fail, implement minimally, watch it pass, commit.
- **Hermetic tests:** no live API/AWS calls. Inject clients via constructor; mock HTTP with `respx`,
  AWS with `moto`. Use fixtures under `tests/fixtures/`.
- **Types:** `common/` is mypy-strict. Match model/field/method names to the TRD exactly.
- **Commit message:** `feat(<area>): <what>` or `test(<area>): <what>`; end with the
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer.
- **Branching:** each task on its own branch `m0/T<NN>-<slug>`; orchestrator merges after review.
- **Definition of done (per task):** listed acceptance criteria met, `pytest` green,
  `ruff check` + `mypy src/gw_geo/common` clean, committed.

## Task files
- [`M0-T01-tooling-ci.md`](M0-T01-tooling-ci.md)
- [`M0-T02-domain-models.md`](M0-T02-domain-models.md)
- [`M0-T03-config.md`](M0-T03-config.md)
- [`M0-T04-db-migrations.md`](M0-T04-db-migrations.md)
- [`M0-T05-cost-governor.md`](M0-T05-cost-governor.md)
- [`M0-T06-engine-adapter-base.md`](M0-T06-engine-adapter-base.md)
- [`M0-T07-parse.md`](M0-T07-parse.md)
- [`M0-T08-perplexity-adapter.md`](M0-T08-perplexity-adapter.md)
- [`M0-T09-openai-adapter.md`](M0-T09-openai-adapter.md)
- [`M0-T10-adapter-contract-tests.md`](M0-T10-adapter-contract-tests.md)
- [`M0-T11-discover.md`](M0-T11-discover.md)
- [`M0-T12-aggregate.md`](M0-T12-aggregate.md)
- [`M0-T13-runner.md`](M0-T13-runner.md)
- [`M0-T14-cli-lambda.md`](M0-T14-cli-lambda.md)
