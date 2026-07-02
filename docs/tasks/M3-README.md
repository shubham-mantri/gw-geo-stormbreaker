# M3 Implementation Tasks — gw-geo-stormbreaker

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` to
> implement these task-by-task. Each task file is a self-contained unit of work for one subagent,
> written TDD-first. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal (M3):** Execution, on-site. Turn measurement into action: **Ranking ML** (what content earns
citations, per engine) + the **on-site Content engine** (grounded generation → guardrails → human
approval gate → publish) + the **Opportunities queue**. This is the milestone where Athena's
plagiarism/hallucination failure is designed out — **guardrails and the approval gate are
first-class, independently-tested requirements**. See [`../m3-design.md`](../m3-design.md) for the
full design and [`../prd.md`](../prd.md) §6.3/§6.4/§6.7.

**Read before starting any task:** [`../m3-design.md`](../m3-design.md),
[`../prd.md`](../prd.md) (§6.3, §6.4, §6.7), [`../trd.md`](../trd.md) (§8, §9, §7, §12),
[`../ui-spec.md`](../ui-spec.md) (§3.4, §3.5, §6). Honor the interface contracts in the design spec
and the ui-spec §6 API shapes exactly — parallel agents depend on them.

---

## Execution model (for the orchestrator)

Dispatch by **wave**. Within a wave, tasks are independent → run subagents **in parallel**.
Between waves, the orchestrator reviews merged output (tests green, interfaces match the design spec)
before starting the next wave. Every task ends with its own commit.

```
Wave 0 (parallel, no deps):    T01  T02  T03
Wave 1 (primitives):           T04  T05  T06  T07  T08  T09  T10
Wave 2 (models/gen/guardrails):T11  T12  T13  T14  T15  T16  T17  T18
Wave 3 (integration):          T19  T20  →  T22  →  T21
```

### Dependency DAG
| Task | Depends on |
|---|---|
| T01 config & secrets | M0 config |
| T02 migrations | M0 db |
| T03 M3 domain models | M0 models |
| T04 feature extraction | T03 |
| T05 labels + dataset | T03 |
| T06 knowledge base | T03 |
| T07 originality guardrail | T03 |
| T08 brand-voice guardrail | T03 |
| T09 publish base + metadata | T03 |
| T10 API scaffold + auth | M0 db, T03 |
| T11 per-engine model | T04, T05 |
| T12 recommendations | T11 |
| T13 bandit | T03, T02 |
| T14 conditioned generation | T06 |
| T15 claim-verification guardrail | T06 |
| T16 guardrail runner + gate policy | T07, T08, T15 |
| T17 approval gate | T03, T02 |
| T18 publishing connectors | T09 |
| T19 opportunities service | T12 |
| T20 ranking runner + CLI | T11, T12, T02 |
| T21 opportunities API | T10, T19, T22 |
| T22 content API + pipeline | T10, T14, T16, T17, T18 |

---

## Conventions (all tasks)
- **TDD:** write the failing test first, watch it fail, implement minimally, watch it pass, commit.
- **Hermetic tests:** no live LLM/embedding/vector-store/CMS/AWS calls. Inject every client via
  constructor/argument; mock HTTP with `respx`, AWS with `moto`, DB with SQLite; use FastAPI
  `TestClient` + `dependency_overrides` for the API. Fixtures under `tests/fixtures/`.
- **Guardrails-first:** originality, claim-verification, brand-voice, and the approval gate are
  hard requirements. A change that lets content publish without a passing `GuardrailReport` **and**
  an authorized human approval is a defect, not a feature.
- **Types:** `common/` is mypy-strict. Match model/field/method names to the design spec exactly.
- **White-hat only** (PRD NG1): no hidden text, cloaking, or bot-divergent content, ever.
- **Commit message:** `feat(<area>): <what>` or `test(<area>): <what>`; end with the
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer.
- **Branching:** each task on its own branch `m3/T<NN>-<slug>`; orchestrator merges after review.
- **Definition of done (per task):** listed acceptance criteria met, `pytest` green,
  `ruff check` + `mypy src/gw_geo/common` clean, committed. **Do NOT git commit as part of authoring
  these docs** — commits happen at implementation time.

## Task files
- [`M3-T01-config-secrets.md`](M3-T01-config-secrets.md)
- [`M3-T02-migrations.md`](M3-T02-migrations.md)
- [`M3-T03-domain-models.md`](M3-T03-domain-models.md)
- [`M3-T04-feature-extraction.md`](M3-T04-feature-extraction.md)
- [`M3-T05-labels-dataset.md`](M3-T05-labels-dataset.md)
- [`M3-T06-knowledge-base.md`](M3-T06-knowledge-base.md)
- [`M3-T07-originality-guardrail.md`](M3-T07-originality-guardrail.md)
- [`M3-T08-brand-voice-guardrail.md`](M3-T08-brand-voice-guardrail.md)
- [`M3-T09-publish-base-metadata.md`](M3-T09-publish-base-metadata.md)
- [`M3-T10-api-scaffold.md`](M3-T10-api-scaffold.md)
- [`M3-T11-per-engine-model.md`](M3-T11-per-engine-model.md)
- [`M3-T12-recommendations.md`](M3-T12-recommendations.md)
- [`M3-T13-bandit.md`](M3-T13-bandit.md)
- [`M3-T14-conditioned-generation.md`](M3-T14-conditioned-generation.md)
- [`M3-T15-claim-verification-guardrail.md`](M3-T15-claim-verification-guardrail.md)
- [`M3-T16-guardrail-runner.md`](M3-T16-guardrail-runner.md)
- [`M3-T17-approval-gate.md`](M3-T17-approval-gate.md)
- [`M3-T18-publishing-connectors.md`](M3-T18-publishing-connectors.md)
- [`M3-T19-opportunities-service.md`](M3-T19-opportunities-service.md)
- [`M3-T20-ranking-runner-cli.md`](M3-T20-ranking-runner-cli.md)
- [`M3-T21-opportunities-api.md`](M3-T21-opportunities-api.md)
- [`M3-T22-content-api-pipeline.md`](M3-T22-content-api-pipeline.md)
