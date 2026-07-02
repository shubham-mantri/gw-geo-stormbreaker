# TRD — gw-geo-stormbreaker

**Technical Requirements & Design Document**
**Status:** Draft v1 · **Owner:** dev@gushwork.ai · **Last updated:** 2026-07-02
**Companion to:** [`prd.md`](prd.md) (the "what/why") · this doc is the "how".

---

## 1. Scope of this TRD

Covers the technical design of all seven subsystems, with **M0 and M1 at implementation
depth** (they are what agents build first) and M2–M4 at design-intent depth. The
per-task implementation breakdown lives in [`tasks/`](tasks/).

**M0 deliverable (definition of done):** a tested, runnable pipeline that, given a brand +
seed topics, builds a prompt set, probes ≥2 engines N× each, parses answers, aggregates to a
visibility snapshot with confidence intervals, and persists it to Postgres — invokable via a
CLI and a Lambda handler, under a per-tenant sampling budget.

---

## 2. System context (self-contained)

Independent project — no external/shared services, no cross-repo coupling.

- **Language/runtime:** Python 3.13 (backend); TypeScript/Next.js (dashboard, §11 / `ui-spec.md`).
- **Compute:** async workers on AWS Lambda + Step Functions (Serverless Framework v4) — or plain
  containers; the code is deploy-target-agnostic behind the runner interface.
- **Datastores:** PostgreSQL (system of record), a vector store (pgvector or Pinecone) for
  semantic-match features and content grounding, S3-compatible object storage (raw answer payloads).
- **Async:** SQS (or any queue) for probe fan-out; Step Functions (or a workflow lib) for the loop.
- **Auth (when API/UI land):** self-contained — JWT + lightweight RBAC, or a hosted provider
  (Clerk/Auth0). Not in M0.
- **Secrets:** environment / SSM, injected at deploy (no secrets in repo).

**Repo shape:** a single self-contained project. This root is the **Python backend + API**; the
dashboard lives in a `web/` app (or sibling frontend), talking to the API over HTTP. DB schema is
managed in-repo via Alembic. No shared/platform migration or auth repos.

---

## 3. Tech stack decisions (with rationale)

| Concern | Choice | Rationale |
|---|---|---|
| Domain models | **Pydantic v2** | Validation at boundaries; serialization for SQS/S3; matches modern Python Lambda code |
| DB access | **SQLAlchemy 2.0 (core + ORM)** + **Alembic** | Typed models, explicit migrations, standard in Python serverless |
| Engine API clients | Official SDKs where present (OpenAI, Google GenAI, Anthropic), `httpx` for the rest (Perplexity Sonar, DeepSeek) | Reliability; async support via `httpx.AsyncClient`; self-contained, no shared client lib |
| Headless capture (M1+) | **Playwright** (async) for consumer surfaces (ChatGPT UI, Google AI Overviews, Grok) | AI Overviews/consumer ChatGPT have no citation-returning API |
| Sentiment / extraction | LLM-based extractor (Claude) with a strict JSON schema | Robust to answer-format variety; deterministic via tool/JSON mode |
| Stats | `scipy.stats` / `statsmodels` | Wilson confidence intervals for proportions |
| Testing | **pytest** + `pytest-asyncio`; `responses`/`respx` for HTTP mocks; `moto` for AWS | Fast, hermetic unit tests; no live API calls in CI |
| Lint/type | **ruff** + **mypy** (strict on `common/`) | Cheap correctness gate for parallel agents |
| Config | `pydantic-settings` | Env-driven, typed, testable |

**Non-determinism rule (system-wide):** every visibility metric is a **proportion with a
sample size and a Wilson 95% CI**. No single answer is ground truth. This is enforced in the
`aggregate` module and the DB schema (snapshots store `n_samples`, `ci_low`, `ci_high`).

---

## 4. Data model (M0/M1 tables)

Postgres, multi-tenant (`tenant_id` on every row; row-level scoping in the access layer).

```
tenant(id, name, sampling_budget_daily, created_at)
brand(id, tenant_id, name, domain, competitors jsonb, knowledge_base_ref, created_at)
prompt(id, tenant_id, brand_id, text, intent_cluster, geo, persona, volume_estimate, created_at)
probe_run(id, tenant_id, prompt_id, engine, geo, persona, ts, status,
          raw_answer_s3_key, cost_usd, latency_ms)
answer_extraction(id, probe_run_id, brand_mentioned bool, position int null,
                  sentiment text, cited_urls jsonb, competitors_present jsonb, raw_json jsonb)
citation(id, tenant_id, brand_id, url, domain, source_type, engine, prompt_id,
         first_seen, last_seen, seen_count)
visibility_snapshot(id, tenant_id, brand_id, engine, geo, persona, date,
                    mention_rate, citation_rate, avg_position, sentiment_score,
                    share_of_voice, n_samples, ci_low, ci_high)
```

M2+ adds: `session`, `lead`, `attribution_link`, `content_asset`, `seeding_task`,
`feature_model`, `drift_event`, `holdout_cohort` (designed in §8–§10, built later).

**Source-type taxonomy** (tagged at parse time, drives ranking + seeding):
`own_site | reddit | wikipedia | review_site | listicle | news_pr | forum_qa | social | docs | other`.

---

## 5. Subsystem: Measurement (M0/M1) — implementation depth

### 5.1 Discover (`measurement/discover.py`)
`build_prompt_set(brand, seed_topics, size) -> list[Prompt]`
- v0 sources: seed topics + LLM paraphrase/expansion into buyer-intent phrasings; intent
  clustering via embeddings (Pinecone). Volume estimate v0 = traditional-search-volume proxy
  (pluggable; real panel volume is v2).
- Deterministic in tests via injected LLM client (no live calls).

### 5.2 Engine adapter contract (`measurement/probe/base.py`) — **keystone**
Every engine implements one interface; adding an engine = one new adapter, zero core changes.

```python
class ProbeResult(BaseModel):
    engine: str
    answer_text: str
    cited_urls: list[str]
    raw: dict            # full provider payload (also archived to S3)
    latency_ms: int
    cost_usd: float

class EngineAdapter(Protocol):
    name: str
    supports_citations: bool
    async def probe(self, prompt: str, *, geo: str, persona: str | None) -> ProbeResult: ...

# registry
def register(adapter: EngineAdapter) -> None: ...
def get_adapter(name: str) -> EngineAdapter: ...
def all_adapters() -> list[EngineAdapter]: ...
```

M0 adapters (API-based, easiest, return citations): **Perplexity Sonar**, **OpenAI (ChatGPT
API w/ web-search tool)**. M1 adds Gemini, Claude, Copilot/Bing, DeepSeek (API) + Playwright
adapters for Google AI Overviews / consumer ChatGPT / Grok.

### 5.3 Parse (`measurement/parse.py`)
`extract(answer: ProbeResult, brand: Brand) -> AnswerExtraction`
- LLM extractor (Claude, JSON mode) returns: `brand_mentioned`, `position` (rank among named
  options, null if absent), `sentiment` (positive|neutral|negative|comparison), `cited_urls`
  normalized + `source_type` tagged, `competitors_present`.
- URL normalization + domain extraction is pure-Python and unit-tested independently.

### 5.4 Aggregate (`measurement/aggregate.py`)
`aggregate(runs: list[AnswerExtraction]) -> VisibilitySnapshot`
- `mention_rate = mentions / n`, `citation_rate = cited / n`, with **Wilson 95% CI**.
- `avg_position` over runs where present; `sentiment_score` mapped to [-1,1]; `share_of_voice`
  = brand mentions / (brand + competitor mentions).
- **Must** carry `n_samples`, `ci_low`, `ci_high`.

### 5.5 Runner (`measurement/runner.py`)
`run_measurement(brand_id, engines, geos, personas, n_samples) -> list[VisibilitySnapshot]`
Orchestrates: load prompts → for each (prompt, engine, geo, persona) probe `n_samples`× (async,
bounded concurrency) → archive raw to S3 → parse → aggregate per (brand, engine, geo, persona)
→ persist snapshots + citations. Wrapped by the **cost governor** (§7).

### 5.6 Drift canary (M1, `orchestration/drift.py`)
Fixed canary prompt set run daily; alert + flag retrain when known-good citation rates drop
beyond a threshold (design only in M0).

---

## 6. Subsystem: Attribution (M2) — design intent

Four layered mechanisms (strongest→weakest), see PRD §6.2:
1. **Direct referral capture** — detect AI-engine referrers (`chatgpt.com`, `perplexity.ai`,
   `gemini.google.com`, …) on inbound sessions via the product's own lead-capture pixel/SDK; write `attribution_link`.
2. **Citation-to-page linkage** — join `citation.url` (our seeded pages) to AI-referred sessions.
3. **Assisted modeling** — branded-search lift correlated to visibility gains; self-reported
   "how did you hear" ingestion.
4. **Holdout incrementality** — un-optimized prompt/geo cohorts vs optimized; report lift.
Output: pipeline view (`$ influenced`, `$ attributed`, leads, top-converting prompts).
Integrations: HubSpot/Salesforce, GA4, the product's own lead-capture pixel/SDK, offline-conversion upload.

---

## 7. Cross-cutting: cost governor, multi-tenancy, observability

- **Cost governor (`common/budget.py`, M0):** before each probe batch, check tenant's
  remaining daily sampling budget (`tenant.sampling_budget_daily` minus today's `probe_run.cost_usd`
  sum); raise `BudgetExceeded` and degrade gracefully (partial snapshot flagged). Probing is the
  dominant cost — this guard is not optional.
- **Multi-tenancy:** `tenant_id` on all rows; a `TenantScopedSession` wrapper injects the filter;
  no cross-tenant reads. Enforced in `common/db.py`, tested.
- **Observability:** structured JSON logs (tenant, brand, engine, prompt_id, cost); per-engine
  capture-success-rate metric; cost per snapshot. CloudWatch in deploy; stdout in local/CLI.

---

## 8. Ranking ML (M3) — design intent
Interpretable per-engine models (gradient-boosted trees / logistic regression) over content
features (structure, info-density, freshness, domain authority, corroboration count, embedding
similarity). Labels from measurement (cited vs not). Output = ranked "feature factors" + gaps +
per-engine channel recommendations. Generation/placement modeled as a bandit; measurement = reward.

## 9. Content engine (M3) — design intent
Brand knowledge base (grounding) → conditioned generation (Claude/GPT) shaped to learned feature
profile → guardrails (plagiarism, claim-verification vs KB, brand-voice) → **human approval gate**
→ publish (CMS connectors / hosted subdomain). Built in-repo (no external content stack).

## 10. Off-site seeding (M4) — design intent
Target discovery from citation-source map → per-channel briefs + human-in-the-loop placement →
**white-hat compliance rules engine** (per-platform ToS; no astroturf/hidden-text) → corroboration
tracking.

---

## 11. API surface (M2; M0 exposes CLI + Lambda only)
- M0: `python -m gw_geo.cli measure --brand <id> --engines perplexity,openai --n 8` and a
  Lambda handler `handlers/run_measurement.py`.
- M2 REST (this project's API layer): `/brands`, `/prompts`, `/visibility`, `/pipeline`,
  `/opportunities`, `/alerts`; webhooks for lead ingestion; MCP connector for client LLM access.
  Consumed by the `web/` dashboard (see [`ui-spec.md`](ui-spec.md) for the full contract).

---

## 12. Testing strategy
- **TDD, unit-first, hermetic:** no live API/AWS calls in CI. Mock HTTP (`respx`), AWS (`moto`),
  and inject LLM/engine clients via constructor (dependency injection) so adapters/parse/discover
  are testable with fixtures.
- **Contract tests** for the `EngineAdapter` interface: every adapter passes the same suite
  (`tests/measurement/probe/test_adapter_contract.py`) driven by recorded fixtures.
- **Property tests** on aggregation math (CI bounds within [0,1], monotonicity).
- **Golden fixtures:** recorded (sanitized) engine responses under `tests/fixtures/answers/`.
- Coverage gate advisory; `common/` held to mypy strict.

---

## 13. Milestone → module map
| Milestone | Modules built | Shippable outcome |
|---|---|---|
| **M0** | `common/*`, `measurement/{discover,probe/base,probe/perplexity,probe/openai,parse,aggregate,runner}`, `budget`, CLI+Lambda | Visibility snapshot for a brand across 2 engines, tested |
| **M1** | +engine adapters (Gemini/Claude/Copilot/DeepSeek + Playwright surfaces), drift canary, dashboards feed | Measurement GA, ≥8 engines |
| **M2** | `attribution/*`, API repo, integrations | Pipeline view live |
| **M3** | `ranking/*`, `content/*` | Grounded execution + recommendations |
| **M4** | `seeding/*`, self-adaptation, RaaS | Full closed loop |

---

## 14. Open technical items
- OT1. In-house capture-fleet (proxies/accounts) build vs managed — confirmed **in-house** (PRD OQ1);
  M1 needs a proxy/account-pool design doc before Playwright adapters.
- OT2. DB migrations in-repo (Alembic) throughout — self-contained, no external migration repo.
- OT3. Engine list priority: Western engines first; DeepSeek/Doubao gated on APAC client demand (PRD OQ5).
- OT4. Embeddings store: Pinecone (platform standard) vs pgvector — default Pinecone; revisit on cost.
