# M2 Implementation Tasks — gw-geo-stormbreaker

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` to
> implement these task-by-task. Each task file is a self-contained unit of work for one subagent,
> written TDD-first. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal (M2 — Product GA):** the end-user product ships. Build the **attribution engine** (4 layered
mechanisms), the **REST API layer** serving the [`../ui-spec.md`](../ui-spec.md) §6 contract, a live
**`web/` Next.js dashboard** (Overview, Visibility, Sources, Pipeline, Alerts, Settings + onboarding),
**CRM/GA4 integrations**, a **lead-capture pixel/SDK**, and **auth + RBAC + multi-tenancy** enforced
server-side on every request. See [`../m2-design.md`](../m2-design.md) for the design and
[`../trd.md`](../trd.md) §6/§7/§11 for the binding contracts.

**Read before starting any task:** [`../m2-design.md`](../m2-design.md),
[`../ui-spec.md`](../ui-spec.md) (§5 auth, §6 API contract — **binding, verbatim**),
[`../trd.md`](../trd.md) (§6 attribution, §7 tenancy/cost), and [`../m1-design.md`](../m1-design.md) §5
(the `measurement/feed.py` queries the read endpoints consume). Honor the ui-spec §6 shapes exactly —
`web/` depends on them.

---

## Execution model (for the orchestrator)

Dispatch by **wave**. Within a wave, tasks are independent → run subagents **in parallel**. Between
waves, the orchestrator reviews merged output (tests green, interfaces match ui-spec §6 + TRD §7)
before starting the next wave. Every task ends with its own commit.

```
Wave 0 (foundation):            T01  T02
Wave 1 (auth/API/ingest):       T03  T04  T05  T09
Wave 2 (attribution/integr/read-API): T06 T07 T08  T10  T11 T12  T13 T14
Wave 3 (payoff API + dashboard): T15 T16  T17  T18 T19 T20  →  T21
```

Intra-wave dependency notes: in Wave 1, T04 needs T03 (auth) merged → T03 lands first. In Wave 2,
T10 needs T06/T07/T08/T09 → runs late; T13/T14 need only T04 + the M1 feed. In Wave 3, T15 needs T10;
T16 needs T11/T12; T17 needs T04; screens T18–T20 need T17 + their endpoints; T21 last.

### Dependency DAG
| Task | Depends on |
|---|---|
| T01 config & secrets | M0 config |
| T02 migrations (M2 tables) | M0 db |
| T03 auth core + RBAC | T02 |
| T04 API skeleton + tenancy deps | T03 |
| T05 lead-capture pixel + ingestion | T02 |
| T06 direct referral capture | T05 |
| T07 citation-to-page linkage | T05 |
| T08 assisted modeling | T05 |
| T09 holdout incrementality | T02 |
| T10 pipeline aggregation | T06, T07, T08, T09 |
| T11 CRM integration | T05 |
| T12 GA4 integration | T05 |
| T13 brands + overview API | T04 (+ M1 feed) |
| T14 visibility + sources API | T04 (+ M1 feed) |
| T15 pipeline + alerts API | T04, T10 |
| T16 settings/integrations/snippet API | T04, T11, T12, T05 |
| T17 web scaffold + auth + API client | T04 |
| T18 Overview + Visibility screens | T17, T13, T14 |
| T19 Sources + Pipeline screens | T17, T14, T15 |
| T20 Alerts + Settings + onboarding | T17, T15, T16 |
| T21 E2E + tenancy validation | T18, T19, T20 |

---

## Conventions (all tasks)
- **TDD:** write the failing test first, watch it fail, implement minimally, watch it pass, commit.
- **Hermetic tests:** no live API/AWS calls. Inject clients via constructor; mock HTTP with `respx`,
  AWS with `moto`, DB with SQLite. `web/`: Vitest/RTL + Playwright against a **mocked** API.
- **Tenancy is server-enforced (TRD §7):** every endpoint/query derives `tenant_id` from the JWT and
  runs through `TenantScopedSession`. Every API task ships a **cross-tenant isolation test**.
- **Attribution honesty:** never expose a pipeline number without its `method_breakdown` +
  confidence note; holdout incrementality is the only causal claim (PRD §13).
- **Types:** `common/` is mypy-strict; match model/field/method names to `m2-design.md` + ui-spec §6.
- **Commit message:** `feat(<area>): <what>` or `test(<area>): <what>`; end with the
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer.
- **Branching:** each task on its own branch `m2/T<NN>-<slug>`; orchestrator merges after review.
- **Definition of done (per task):** listed acceptance criteria met, `pytest` green (and `web/`
  tests green for frontend tasks), `ruff check` + `mypy src/gw_geo/common` clean, committed.

## Task files
- [`M2-T01-config-secrets.md`](M2-T01-config-secrets.md)
- [`M2-T02-migrations.md`](M2-T02-migrations.md)
- [`M2-T03-auth-core.md`](M2-T03-auth-core.md)
- [`M2-T04-api-skeleton.md`](M2-T04-api-skeleton.md)
- [`M2-T05-lead-capture-pixel.md`](M2-T05-lead-capture-pixel.md)
- [`M2-T06-direct-referral.md`](M2-T06-direct-referral.md)
- [`M2-T07-citation-linkage.md`](M2-T07-citation-linkage.md)
- [`M2-T08-assisted-modeling.md`](M2-T08-assisted-modeling.md)
- [`M2-T09-holdout-incrementality.md`](M2-T09-holdout-incrementality.md)
- [`M2-T10-pipeline-aggregation.md`](M2-T10-pipeline-aggregation.md)
- [`M2-T11-crm-integration.md`](M2-T11-crm-integration.md)
- [`M2-T12-ga4-integration.md`](M2-T12-ga4-integration.md)
- [`M2-T13-brands-overview-api.md`](M2-T13-brands-overview-api.md)
- [`M2-T14-visibility-sources-api.md`](M2-T14-visibility-sources-api.md)
- [`M2-T15-pipeline-alerts-api.md`](M2-T15-pipeline-alerts-api.md)
- [`M2-T16-settings-integrations-api.md`](M2-T16-settings-integrations-api.md)
- [`M2-T17-web-scaffold.md`](M2-T17-web-scaffold.md)
- [`M2-T18-overview-visibility-screens.md`](M2-T18-overview-visibility-screens.md)
- [`M2-T19-sources-pipeline-screens.md`](M2-T19-sources-pipeline-screens.md)
- [`M2-T20-alerts-settings-onboarding.md`](M2-T20-alerts-settings-onboarding.md)
- [`M2-T21-e2e-tenancy.md`](M2-T21-e2e-tenancy.md)
