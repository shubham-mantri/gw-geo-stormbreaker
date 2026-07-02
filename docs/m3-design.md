# M3 Design — Execution (Ranking ML + On-Site Content Engine + Opportunities)

**Design spec for Milestone 3** · **Status:** Draft v1 · **Owner:** dev@gushwork.ai · **Date:** 2026-07-02
**Companion to:** [`prd.md`](prd.md) (§6.3 Feature/Rank ML, §6.4 Content Engine, §6.7 Opportunity queue),
[`trd.md`](trd.md) (§8 Ranking ML, §9 Content engine, §7 cost/tenancy, §12 testing),
[`ui-spec.md`](ui-spec.md) (§3.4 Opportunities, §3.5 Content workspace, §6 `/opportunities` + `/content` API).
This spec is the input to the M3 task breakdown (`tasks/M3-*.md`).

---

## 1. Goal (definition of done)

**Execution, on-site:** turn the measurement system of record (M0/M1) into *action*. M3 delivers three
subsystems that together answer "what content earns citations, and let a human ship it safely":

1. **Ranking ML** (`src/gw_geo/ranking/`) — per-engine, **interpretable** models that learn which
   content features predict being cited (labels from measurement), emitting ranked **feature factors**,
   **content gaps**, and **per-engine channel recommendations**; generation/placement is framed as a
   **bandit** (arms = content-variant × channel, reward = measurement).
2. **On-site Content engine** (`src/gw_geo/content/`) — brand **knowledge base** (grounding, vector
   store) → **conditioned generation** → **guardrails** (originality/plagiarism, claim-verification vs
   KB, brand-voice) → **human approval gate** → **publishing connectors** (WordPress / Webflow /
   Framer / headless + a product-hosted subdomain) with schema/freshness metadata.
3. **Opportunities queue** (`src/gw_geo/orchestration/opportunities.py`) — rank gaps from the ranking
   output + measurement snapshots into a prioritized to-do list, and the `/opportunities → /content`
   flow (ui-spec §3.4/§3.5, §6).

**This is the milestone where Athena's documented plagiarism/hallucination failure is designed out.**
Guardrails (originality, claim-verification, brand-voice) and the human approval gate are **first-class,
independently-tested requirements** — nothing publishes without passing guardrails *and* an explicit
human click (ui-spec §3.5 "approval gates are explicit").

**Non-goals for M3** (per PRD roadmap): off-site seeding placement + compliance engine, drift-triggered
retraining, and RaaS pricing are **M4**. M3 builds the *models, generation, guardrails, gate, publish,
and the opportunity queue that feeds them* — white-hat only (PRD NG1).

M3 adds **zero changes to the M0/M1 measurement contracts**. It **consumes** them: `VisibilitySnapshot`,
`Citation`, `AnswerExtraction`, `SourceType` (labels + channel mix) are inputs; the new subsystems
attach at the read edge and never mutate measurement rows.

---

## 2. Subsystem A — Ranking ML (`src/gw_geo/ranking/`)

**Approach (PRD §6.3, TRD §8): start interpretable, not black-box.** Per-engine models over content
features; labels from measurement; output = actionable levers, not a score nobody can act on.

### 2.1 Feature extraction (`ranking/features.py`)
Pure-Python + one **injected** embedding client (no live calls). Extracts, for a `(page/asset, prompt,
engine)`, a `FeatureVector`:

| Feature | Meaning | Source |
|---|---|---|
| `structure_score` | definition-first opening, FAQ/schema, tables, listicle shape | DOM/markdown heuristics |
| `info_density` | stats/numbers per 100 words | tokenizer + numeric-token count |
| `freshness_days` | age vs `now` from datePublished/dateModified | metadata (None if unknown) |
| `domain_authority` | source-domain authority proxy | injected/precomputed table |
| `corroboration_count` | # independent domains carrying consistent facts | measurement citation graph |
| `embedding_similarity` | cosine(content, prompt intent) | injected `EmbeddingClient` |
| `has_schema` / `has_faq` / `table_count` | extraction-friendly format signals | markup heuristics |

```python
class EmbeddingClient(Protocol):
    def embed(self, text: str) -> list[float]: ...

def extract_features(*, content: str, prompt_text: str, domain_authority: float,
                     corroboration_count: int, published_at: str | None,
                     embedder: EmbeddingClient, now: str) -> FeatureVector: ...
```

### 2.2 Labels from measurement (`ranking/labels.py`, `ranking/dataset.py`)
A page/URL is **cited (label=1)** for `(brand, engine)` iff it appears in that engine's `citation` rows
(M0 `Citation`); else 0. `build_dataset` joins candidate `(url, content, prompt, meta)` rows with the
cited-URL set to produce `list[LabeledExample]`. DB reads are **tenant-scoped** (TRD §7) via a thin
helper; the join itself is a pure function (testable without a DB).

```python
def cited_urls_for(session, *, tenant_id: str, brand_id: str, engine: str) -> set[str]: ...
def build_dataset(candidates: list[dict], cited_urls: set[str], *, engine: str,
                  feature_fn) -> list[LabeledExample]: ...
```

### 2.3 Per-engine model (`ranking/model.py`)
One `EngineRankingModel` per engine. **Backend is injected** (`ModelBackend` Protocol) so tests use a
deterministic fake; the real backend wraps scikit-learn **GradientBoostingClassifier** (default) or
**LogisticRegression** (config `ranking_model_type`). Emits `feature_importances` → interpretable
`FeatureFactor`s ("`has_schema` + `info_density≥3` → +X citation prob").

```python
class ModelBackend(Protocol):
    def fit(self, X: list[list[float]], y: list[int]) -> None: ...
    def predict_proba(self, X: list[list[float]]) -> list[float]: ...
    def feature_importances(self) -> list[float]: ...

class EngineRankingModel:
    def __init__(self, engine: str, feature_names: list[str], backend: ModelBackend) -> None: ...
    def train(self, examples: list[LabeledExample]) -> None: ...
    def predict(self, fv: FeatureVector) -> float: ...        # citation probability
    def importances(self) -> list[FeatureFactor]: ...          # ranked factors
```

### 2.4 Recommendations (`ranking/recommend.py`)
Turns a trained model + the current asset + the engine's citation-source mix (from M0/M1 measurement)
into a `RankingReport`: ranked **factors**, **gaps** (factor below target), **per-engine channel
recommendations** ("Perplexity pulls from Reddit here → seed Reddit"; channel = `SourceType`).

```python
def build_report(model: EngineRankingModel, current: FeatureVector,
                 source_mix: dict[SourceType, float]) -> RankingReport: ...
```

### 2.5 Bandit (`ranking/bandit.py`)
Generation/placement as a **bandit** (PRD §6.3): each **arm = (content_variant, channel)**; **reward =
measurement** (mention/citation-rate uplift ∈ [0,1]). Thompson sampling over a Beta posterior per arm
(`alpha`/`beta`); `select()` chooses the next arm, `update(arm_id, reward)` folds in the measured
result. RNG is **injected** for deterministic tests. Persisted to `bandit_arm` / `bandit_reward`.

```python
class Bandit:
    def __init__(self, arms: list[BanditArm], *, rng=None) -> None: ...
    def select(self) -> BanditArm: ...
    def update(self, arm_id: str, reward: float) -> None: ...
```

### 2.6 Ranking runner + CLI (`ranking/runner.py`)
`run_ranking(session, tenant_id, brand_id, engines, …)` builds datasets from measurement, trains one
model per engine, persists `feature_model` rows, and returns `{engine: RankingReport}`. Exposed as
`python -m gw_geo.cli rank --brand <id> --engines perplexity,openai`.

---

## 3. Subsystem B — On-site Content engine (`src/gw_geo/content/`)

Pipeline: **KB grounding → conditioned generation → guardrails → human approval gate → publish**
(PRD §6.4, TRD §9). Every external dependency (LLM, embeddings, vector store, plagiarism corpus, CMS
HTTP) is **injected** — the whole pipeline is hermetically testable with fakes.

### 3.1 Brand knowledge base (`content/kb.py`)
Per-brand source of truth (approved facts, USPs, products, pricing, certifications, claims). Backed by a
`VectorStore` Protocol (real impl = Pinecone/pgvector, TRD §2/OT4) + an injected `EmbeddingClient`.
`ground(query)` returns the top-k supporting `Fact`s — the anti-hallucination substrate.

```python
class VectorStore(Protocol):
    def upsert(self, id: str, vector: list[float], meta: dict[str, Any]) -> None: ...
    def query(self, vector: list[float], top_k: int) -> list[tuple[str, float, dict[str, Any]]]: ...

class KnowledgeBase:
    def __init__(self, *, brand_id: str, store: VectorStore, embedder: EmbeddingClient) -> None: ...
    def add_fact(self, fact: Fact) -> None: ...
    def ground(self, query: str, *, top_k: int = 5) -> list[Fact]: ...
```

### 3.2 Conditioned generation (`content/generate.py`)
An **injected** `LLMClient` (Claude/GPT via JSON mode; see `claude-api` skill for the Anthropic
Messages contract) produces content shaped to the target engine's learned feature profile
(`RankingReport`) + intent cluster, formatted for extraction (direct-answer block, FAQ/HowTo JSON-LD,
comparison tables, quantified stats). Generation is **grounded**: the prompt carries only KB facts, and
the returned `ContentDraft` records `grounded_fact_ids`. No live calls in tests.

```python
class LLMClient(Protocol):
    def complete(self, *, system: str, prompt: str, schema: dict[str, Any] | None = None) -> dict[str, Any]: ...

def generate_draft(*, brand: Brand, prompt_text: str, facts: list[Fact],
                   feature_profile: RankingReport | None, llm: LLMClient,
                   target_engine: str | None = None, intent_cluster: str | None = None,
                   id_fn=None) -> ContentDraft: ...
```

### 3.3 Guardrails (`content/guardrails/`) — **first-class, this is the Athena fix**
Three independent checks + an aggregator. Each returns `(ok, score, details)`; the aggregator ANDs them
into a `GuardrailReport` whose `passed` flag is a **hard precondition** for the approval gate.

- **Originality / plagiarism** (`guardrails/originality.py`): k-shingling + Jaccard against an injected
  `CorpusSearch` (web/corpus). `ok = max_similarity < originality_threshold` (default 0.25). *This is
  the specific check that would have caught Athena's plagiarism.*
- **Claim-verification vs KB** (`guardrails/claims.py`): an injected `ClaimExtractor` pulls factual
  claims from the draft; each must be **grounded** in the KB (`kb.ground` returns support above
  `claim_sim_threshold`, default 0.8) or it is reported as `unverified`. Any unverified claim →
  `claims_ok = False`. *No fabricated stats reach publish.*
- **Brand-voice** (`guardrails/brand_voice.py`): an injected `VoiceScorer` scores conformance to the
  brand voice profile; `ok = score ≥ brand_voice_min` (default 0.7).
- **Runner** (`guardrails/runner.py`): `run_guardrails(...) -> GuardrailReport`;
  `passed = originality_ok and claims_ok and brand_voice_ok`. Persisted to `content_guardrail_report`
  for audit.

```python
def check_originality(draft_text: str, *, corpus: CorpusSearch,
                      threshold: float = 0.25) -> tuple[bool, float, list[str]]: ...
def verify_claims(draft_text: str, *, kb: KnowledgeBase, extractor: ClaimExtractor,
                  sim_threshold: float = 0.8) -> tuple[bool, list[str]]: ...
def check_brand_voice(draft_text: str, voice_profile: dict[str, Any], *, scorer: VoiceScorer,
                      min_score: float = 0.7) -> tuple[bool, float, list[str]]: ...
def run_guardrails(draft: ContentDraft, *, kb, corpus, extractor, voice_scorer,
                   voice_profile, thresholds) -> GuardrailReport: ...
```

### 3.4 Human approval gate (`content/approval.py`)
A small state machine: `DRAFT → PENDING_REVIEW → APPROVED → PUBLISHED` (or `REJECTED`). `approve()`
requires **(a)** a `GuardrailReport.passed is True` **and (b)** a role in `{editor, admin, owner}`
(ui-spec §5 RBAC) — otherwise `ApprovalError`. `ensure_publishable()` raises unless status is
`APPROVED`. **Publishing cannot bypass the gate** — enforced here and asserted in tests.

```python
class ApprovalError(Exception): ...
def submit_for_review(draft: ContentDraft) -> ContentDraft: ...
def approve(draft: ContentDraft, *, report: GuardrailReport, role: str) -> ContentDraft: ...
def ensure_publishable(draft: ContentDraft) -> None: ...   # raises unless APPROVED
```

### 3.5 Publishing connectors (`content/publish/`)
`PublishConnector` Protocol; concrete connectors for **WordPress**, **Webflow**, **Framer**,
**headless/API**, and a **product-hosted subdomain**. HTTP via an **injected** `httpx.AsyncClient`
(`respx`-mocked in tests, exactly like M0/M1 API adapters). Each injects **schema/freshness metadata**
(JSON-LD Article/FAQ, `datePublished`/`dateModified`) via `publish/metadata.py` and triggers sitemap
resubmission where applicable.

```python
class PublishResult(BaseModel):
    published_url: str; external_id: str; connector: str

class PublishConnector(Protocol):
    name: str
    async def publish(self, draft: ContentDraft, *, freshness: dict[str, Any]) -> PublishResult: ...

def get_connector(name: str) -> PublishConnector: ...     # registry, mirrors probe.base
def build_jsonld(draft: ContentDraft, *, published: str, modified: str) -> dict[str, Any]: ...
```

### 3.6 Content pipeline (`content/pipeline.py`)
Orchestrates §3.1–§3.5 for one draft: `ground → generate → run_guardrails → persist content_asset →
(gate) → publish`. This is what the `/content` API endpoints call.

---

## 4. Subsystem C — Opportunities queue (`orchestration/opportunities.py`)

Ranks gaps from the ranking reports + measurement snapshots into `Opportunity` rows (ui-spec §3.4).
Gap sources: **absence** (low `mention_rate`/`citation_rate` engines), **source gaps** (competitor cited
on a domain/`SourceType` where the brand is not — from the citation-source mix), and **sentiment**
(neutral/negative on an engine → "add proof/data"). Ranked by `est_impact` (∝ engine weight × gap size).

```python
def build_opportunities(*, brand: Brand, snapshots: list[VisibilitySnapshot],
                        reports: list[RankingReport], source_mix: dict[str, Any],
                        id_fn=None) -> list[Opportunity]: ...
```

The **`/opportunities → /content` flow** (ui-spec §3.4 "Fix this ▸"): `POST /opportunities/{id}/act`
marks the opportunity `acted` and spawns a pre-scoped `ContentDraft` via the content pipeline, returning
`{content_id}` (ui-spec §6).

---

## 5. API surface (M3 slice — ui-spec §6)

M3 exposes the **`/opportunities` and `/content`** endpoints in-repo. A lightweight **FastAPI** app
(`src/gw_geo/api/`) with a tenant/role dependency (`Principal` from the bearer token; server-enforced
scope, ui-spec §5) hosts M3's routers. Services are **injected** so the routers are tested with
FastAPI `TestClient` + `dependency_overrides` (no DB/LLM/HTTP live calls). Response shapes match
ui-spec §6 exactly:

| Method & path | Purpose | Returns |
|---|---|---|
| `GET /brands/{id}/opportunities` | ranked gaps (3.4) | `[{id,title,rationale,est_impact,engine}]` |
| `POST /opportunities/{id}/act` | spawn content from a gap | `{content_id}` |
| `POST /content/generate` | draft for a prompt/opportunity | `{content_id,draft,guardrails:{claims_ok,originality_ok}}` |
| `POST /content/{id}/approve` | human gate | `{status}` |
| `POST /content/{id}/publish` | publish approved draft | `{status,published_url}` |

> The full read API (Overview/Visibility/Sources/Pipeline) is **M2**. M3 builds only the two write-heavy
> surfaces it owns; the app factory is structured so M2's routers mount alongside without change.

---

## 6. Cross-cutting

- **Consumes measurement, never mutates it.** Labels (`Citation`), channel mix (`AnswerExtraction`
  source-types), snapshots (`VisibilitySnapshot`) are read-only inputs. Reads go through the
  `TenantScopedSession` (TRD §7) — no cross-tenant leakage of models, drafts, or opportunities.
- **Injected clients everywhere (hermetic, TRD §12).** Embedding client, LLM client, vector store,
  model backend, plagiarism corpus, claim-extractor, voice scorer, and CMS `httpx.AsyncClient` are all
  Protocols supplied by the caller. Real impls live beside the fakes but are **never exercised in the
  default suite** (mirrors M0/M1: `respx` for HTTP, injected LLM stubs).
- **Config (`Settings`) additions:** `vector_store: str = "pinecone"`, `pinecone_api_key`,
  `pinecone_index`, `embedding_model`, `ranking_model_type: str = "gbt"`,
  `originality_threshold: float = 0.25`, `claim_sim_threshold: float = 0.8`,
  `brand_voice_min: float = 0.7`, and publishing creds (`wordpress_base_url`, `wordpress_token`,
  `webflow_token`, `webflow_site_id`, `framer_token`, `headless_publish_url`,
  `hosted_subdomain_base: str = "kb.example.com"`). (`openai_api_key`/`anthropic_api_key` already exist.)
- **Data model additions (Alembic migration `0003_m3`):**
  - `feature_model(id, tenant_id, brand_id, engine, model_type, feature_names jsonb, importances jsonb, metrics jsonb, trained_at)` — one trained model artifact per (tenant, brand, engine).
  - `content_asset(id, tenant_id, brand_id, type, target_engine, prompt_id, title, body_s3_key, features jsonb, schema_jsonld jsonb, status, published_url, connector, published_at, created_at)` — PRD §7 `content_asset`.
  - `content_guardrail_report(id, tenant_id, content_asset_id, originality_ok, originality_score, claims_ok, unverified_claims jsonb, brand_voice_ok, brand_voice_score, passed, ts)` — audit trail for the gate.
  - `opportunity(id, tenant_id, brand_id, title, rationale, engine, est_impact, source_gap, status, created_at)`.
  - `bandit_arm(id, tenant_id, brand_id, content_variant, channel, alpha, beta, pulls, updated_at)` + `bandit_reward(id, tenant_id, arm_id, reward, source_snapshot_id, ts)`.
  All tenant-scoped (TRD §7). (Assumes M1's `0002` migration precedes; if M1 is unmerged, this is `0002`.)
- **Deps:** add `scikit-learn` (ranking backends), `fastapi` + `httpx`/`starlette` TestClient (API),
  a vector-store client (Pinecone SDK or `pgvector`, config-selected, OT4). No per-vendor content stack
  (built in-repo, PRD §6.4). LLM/embeddings via existing keys.
- **White-hat only (PRD NG1):** generation is grounded + originality-checked; **no** hidden text,
  cloaking, or divergent bot content ever enters the codebase. Off-site placement is M4.
- **Conventions (unchanged from M0/M1):** branch `m3/T<NN>-<slug>`, TDD, hermetic tests, mypy-strict on
  `common/`, `Co-Authored-By: Claude Opus 4.8 (1M context)` trailer, per-task commit, orchestrator
  merges per wave. Everything local (no remote push).

---

## 7. Task DAG & waves (~22 tasks)

| Task | Depends on | Summary |
|---|---|---|
| M3-T01 config & secrets | M0 config | vector store, embedding/model config, guardrail thresholds, publish creds |
| M3-T02 migrations | M0 db | `feature_model`, `content_asset`, `content_guardrail_report`, `opportunity`, `bandit_arm`, `bandit_reward` |
| M3-T03 M3 domain models | M0 models | `FeatureVector`, `LabeledExample`, `RankingReport`, `Fact`, `ContentDraft`, `GuardrailReport`, `Opportunity`, `BanditArm` |
| M3-T04 feature extraction | T03 | `ranking/features.py` (structure/density/freshness/authority/corroboration/embedding) |
| M3-T05 labels + dataset | T03 | `ranking/labels.py` + `dataset.py` (cited-vs-not from measurement) |
| M3-T06 knowledge base | T03 | `content/kb.py` grounding over injected vector store |
| M3-T07 originality guardrail | T03 | `content/guardrails/originality.py` (shingling/Jaccard vs corpus) |
| M3-T08 brand-voice guardrail | T03 | `content/guardrails/brand_voice.py` |
| M3-T09 publish base + metadata | T03 | `content/publish/base.py` + `metadata.py` (JSON-LD/freshness) |
| M3-T10 API scaffold + auth | M0 db, T03 | `api/app.py` + `api/deps.py` (Principal, tenant/role scope) |
| M3-T11 per-engine model | T04, T05 | `ranking/model.py` (GBT/logreg via injected backend) |
| M3-T12 recommendations | T11 | `ranking/recommend.py` (factors + gaps + channel recs) |
| M3-T13 bandit | T03, T02 | `ranking/bandit.py` (arms=variant×channel, reward=measurement) |
| M3-T14 conditioned generation | T06 | `content/generate.py` (grounded, feature-shaped) |
| M3-T15 claim-verification guardrail | T06 | `content/guardrails/claims.py` (verify vs KB) |
| M3-T16 guardrail runner + gate policy | T07, T08, T15 | `content/guardrails/runner.py` → `GuardrailReport` |
| M3-T17 approval gate | T03, T02 | `content/approval.py` (state machine + RBAC + guardrail precondition) |
| M3-T18 publishing connectors | T09 | WordPress/Webflow/Framer/headless/hosted via injected httpx |
| M3-T19 opportunities service | T12 | `orchestration/opportunities.py` (rank gaps) |
| M3-T20 ranking runner + CLI | T11, T12, T02 | `ranking/runner.py` + `rank` CLI (train + persist `feature_model`) |
| M3-T21 opportunities API | T10, T19, T22 | `/brands/{id}/opportunities`, `/opportunities/{id}/act` |
| M3-T22 content API + pipeline | T10, T14, T16, T17, T18 | `content/pipeline.py` + `/content/generate\|approve\|publish` |

```
Wave 0 (foundation):    T01  T02  T03
Wave 1 (primitives):    T04  T05  T06  T07  T08  T09  T10
Wave 2 (models/gen/gr): T11  T12  T13  T14  T15  T16  T17  T18
Wave 3 (integration):   T19  T20  →  T22  →  T21
```
Intra-wave note (as in M0/M1): T12 needs T11; T16 needs T07+T08+T15 (so runs late in Wave 2); in Wave 3
T22 (content pipeline/API) must merge before T21 (opportunities `act` spawns content); T20 (ranking
runner) is independent and can land first.

---

## 8. Testing strategy

- **Hermetic CI (TRD §12):** every external client is injected. `respx` for CMS/HTTP publish connectors;
  **fake `ModelBackend`** (deterministic) for ranking models — scikit-learn is the *real* backend, never
  called in the default suite; **stub `LLMClient` / `EmbeddingClient` / `VectorStore` / `ClaimExtractor`
  / `VoiceScorer` / `CorpusSearch`**; SQLite for DB; FastAPI `TestClient` + `dependency_overrides` for
  the API. No live LLM/embedding/CMS calls in `pytest`.
- **Guardrails are the gate — tested as such:** dedicated tests prove (a) plagiarized text fails
  originality, (b) an ungrounded claim fails claim-verification, (c) off-voice text fails brand-voice,
  and (d) `approve()`/publish **raise** when `GuardrailReport.passed is False` or the role lacks
  permission. This is the executable version of "Athena's failure cannot happen here."
- **Ranking math:** feature extraction unit-tested on fixed strings; model importances → `FeatureFactor`
  ordering asserted with the fake backend; bandit determinism asserted via injected RNG.
- **Opportunities/API:** opportunity ranking asserted on seeded snapshots+reports; API response shapes
  asserted against ui-spec §6 (`{content_id, draft, guardrails:{claims_ok, originality_ok}}`, etc.).
- **Property tests:** originality similarity ∈ [0,1]; bandit posterior monotonic under reward.

---

## 9. Confirmed decisions
1. **Interpretable models** (GBT default / logistic regression) with an **injected backend** so tests
   never need scikit-learn at runtime; real backend wraps scikit-learn (TRD §8).
2. **Guardrails + approval gate are first-class, independently tested**, and are a **hard precondition**
   for publish — the designed-in fix for Athena's plagiarism/accuracy failure (PRD §1.2, §13).
3. **Publishing** via an injected-`httpx` connector layer (WordPress/Webflow/Framer/headless/hosted),
   `respx`-mocked — same pattern as M0/M1 API adapters. Built **in-repo** (PRD §6.4).
4. **Bandit** = Thompson sampling over (variant×channel) arms, reward = measurement uplift; persisted to
   `bandit_arm`/`bandit_reward`.
5. **API:** M3 owns `/opportunities` + `/content` (ui-spec §6) via an in-repo FastAPI app; M2's read API
   mounts alongside later.
6. **Final artifact** → `docs/tasks/M3-T*.md` + an `M3` `README` (same format as M0/M1).

## 10. Open items / risks
- **Domain-authority feature** needs a source (precomputed table vs third-party); v1 = injected proxy,
  revisit in M4 with the citation graph.
- **Vector store** choice (Pinecone vs pgvector, TRD OT4) is config-selected; the KB is written against
  the `VectorStore` Protocol so the choice is swappable.
- **Guardrail thresholds** (originality 0.25 / claim-sim 0.8 / voice 0.7) are starting points; they are
  config-driven and will be calibrated on real drafts — conservative-by-default (fail closed).
- **Claim-extractor / voice-scorer** real impls are LLM-based; their prompts must be validated against
  current provider docs when built (see `claude-api` skill). Tests use stubs.
- **Off-site seeding, drift-triggered retraining, RaaS** are explicitly **M4**, not M3.
```
