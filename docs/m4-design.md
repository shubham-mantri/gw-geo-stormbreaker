# M4 Design — Off-Site Seeding · Self-Adaptation · RaaS

**Design spec for Milestone 4** · **Status:** Draft v1 · **Owner:** dev@gushwork.ai · **Date:** 2026-07-02
**Companion to:** [`prd.md`](prd.md) (§6.5 off-site seeding, §6.6 orchestration/self-adaptation, §9 pricing/RaaS, NG1 white-hat)
and [`trd.md`](trd.md) (§10 seeding, §6.3/§8 ranking+bandit, §5.6 drift), [`m1-design.md`](m1-design.md) §4 (drift canary — M4 extends it to retraining),
[`ui-spec.md`](ui-spec.md) (§3.5 seeding tracker, §3.7 alerts, §7 M4 mapping). This spec is the input to the M4 task breakdown (`tasks/M4-*.md`).

---

## 1. Goal (definition of done)

**Close the loop.** M4 makes the platform *act* on what measurement (M1) and attribution (M2) see,
and *keep working* as engines change:

1. **Off-site seeding** (`src/gw_geo/seeding/`) — turn the citation-source map into a queue of
   **human-executed** placement tasks on the third-party sources LLMs cite (Reddit, Quora,
   G2/Capterra, listicles, Wikipedia-eligible facts, PR-wire, expert bylines), each gated by a
   **hard white-hat compliance rules engine** (PRD NG1 — no astroturf/hidden-text/cloaking), with
   corroboration tracking.
2. **Self-adaptation** (`src/gw_geo/orchestration/`) — a **drift-breach → retrain trigger** for the
   ranking models (extends the M1 drift canary), a **bandit optimizer** that allocates content/channel
   effort by measured reward, and a **continuous-loop scheduler** that runs measure → sense → adapt.
3. **Results-as-a-Service (RaaS) pricing/billing** (`src/gw_geo/billing/`) — usage + results-linked
   metering (attributed leads/pipeline from M2), a pricing model, and billing views (PRD §9, OQ4).

M4 adds **zero changes to the M0/M1 core contracts** (`EngineAdapter`, `ProbeResult`, models,
`run_measurement`, `drift_event`). Every new subsystem attaches at the edges and is **fully
decoupled**: cross-milestone inputs (M1 feed / citation-source map, M2 attribution, M3 ranking
models) are consumed through **injected protocols**, so M4 is buildable and testable **before** those
land. Hermetic CI throughout — **no live posting, no live network** (TRD §12); every external action
(placement, publish, retrain) is either a **fake/injected client** or a **human-gated** step.

---

## 2. Subsystem A — Off-site seeding (`src/gw_geo/seeding/`)

Deliberately **service-heavy / workflow-assisted** in v1 (PRD §6.5): the platform discovers targets,
writes briefs, and **routes to a human** for placement — it never auto-posts. The differentiation is
that we do it at all, safely.

### 2.1 Target discovery (`seeding/discovery.py`)
Reads the **citation-source map** (M1 `measurement/feed.citation_source_mix` — injected as a
`SourceMap` protocol) and the `SourceType` taxonomy (TRD §4) to rank high-authority
domains/communities each engine trusts for the brand's prompts, minus where the brand already
corroborates. Emits `SeedingTarget`s (channel + source_type + priority + rationale + est. reward).

```python
class SeedingTarget(BaseModel):
    channel: str            # seeding_channel.name
    source_type: SourceType
    domain: str
    engine: str
    gap_score: float        # competitor_cited_pct - you_cited_pct, clamped ≥0
    priority: float         # gap_score * source authority weight
    rationale: str

class SourceMap(Protocol):                       # satisfied by M1 measurement/feed
    def citation_source_mix(self, *, tenant_id: str, brand_id: str,
                            since: str, until: str) -> dict[str, Any]: ...

def discover_targets(source_map: SourceMap, *, tenant_id: str, brand_id: str,
                     since: str, until: str, channels: "ChannelCatalog",
                     limit: int = 25) -> list[SeedingTarget]: ...
```

### 2.2 Channel catalog (`seeding/channels.py`)
Static, versioned catalog of supported channels, each mapped to a `SourceType`, a ToS ruleset id,
and placement metadata. Persisted to `seeding_channel` (seed migration) and loadable in-process.

| `name` | `source_type` | disclosure req. | UGC | notes |
|---|---|---|---|---|
| `reddit` | `reddit` | yes (affiliation) | yes | 9:1 self-promo rule; no vote manipulation |
| `quora` | `forum_qa` | yes (credential) | yes | disclose affiliation in answer |
| `g2` | `review_site` | yes | yes | genuine reviews only; no incentivized-undisclosed |
| `capterra` | `review_site` | yes | yes | genuine reviews only |
| `listicle` | `listicle` | yes (if sponsored) | no | editorial pitch / sponsored-labeled |
| `wikipedia` | `wikipedia` | yes (COI) | yes | verifiable secondary sources; no paid/self edits |
| `pr_wire` | `news_pr` | n/a | no | factual, non-misleading distribution |
| `expert_byline` | `news_pr` | yes (author relationship) | no | real named author; guest/contributed |

### 2.3 Per-channel briefs (`seeding/briefs.py`)
Given a `SeedingTarget` + brand knowledge base (injected `BriefLLM` protocol — Claude/GPT in prod,
fake in tests), produce a **channel-shaped brief**: talking points grounded in approved facts, the
required **disclosure snippet**, format guidance, and a compliance checklist. Briefs are drafts for a
human; they are **never auto-published**.

```python
class SeedingBrief(BaseModel):
    channel: str; target_url: str | None
    talking_points: list[str]
    grounded_facts: list[str]           # from brand KB — no fabricated stats
    disclosure_text: str                # required affiliation/COI disclosure
    format_notes: str
    compliance_checklist: list[str]

class BriefLLM(Protocol):
    def draft_brief(self, *, target: "SeedingTarget", facts: list[str],
                    disclosure: str) -> dict[str, Any]: ...

def build_brief(llm: BriefLLM, *, target: SeedingTarget, facts: list[str],
                channel: "Channel") -> SeedingBrief: ...
```

### 2.4 White-hat compliance rules engine (`seeding/compliance.py`) — **HARD GATE (PRD NG1)**
The keystone of M4. A deterministic, unit-tested engine that evaluates a proposed placement against
**global** white-hat invariants **and** per-platform ToS rules. **No task may reach `placed` without a
passing report** — enforced in the workflow (§2.5) and asserted in tests. This is a *gate*, not a
warning banner.

**Global invariants (apply to every channel — NG1):**
- `no_astroturf` — no fake/undisclosed identities, sockpuppets, or coordinated inauthentic activity.
- `no_hidden_text` — no hidden/white-on-white/zero-opacity text or keyword stuffing.
- `no_cloaking` — content shown to bots must equal content shown to humans.
- `no_prompt_injection` — no hidden instructions targeting LLM crawlers.
- `disclosure_present` — required affiliation/COI/sponsored disclosure must be present when the
  channel demands it.

```python
class ComplianceRule(BaseModel):
    code: str                       # e.g. "no_astroturf", "reddit_self_promo_ratio"
    channel: str                    # channel name, or "*" for global
    description: str
    severity: Literal["block", "warn"]
    check: str                      # key into the check registry

class ComplianceViolation(BaseModel):
    rule_code: str; severity: str; message: str

class ComplianceReport(BaseModel):
    channel: str
    passed: bool                    # False if ANY block-severity violation
    violations: list[ComplianceViolation]

class PlacementProposal(BaseModel):
    channel: str
    body: str
    disclosure_text: str
    author_is_real: bool            # attests a real, disclosed human actor
    is_paid: bool
    served_to_bots: str | None = None   # if set, must equal body (cloaking check)

class ComplianceEngine:
    def __init__(self, rules: list[ComplianceRule],
                 checks: dict[str, "CheckFn"] | None = None) -> None: ...
    def evaluate(self, proposal: PlacementProposal) -> ComplianceReport: ...
    @staticmethod
    def default_ruleset() -> list[ComplianceRule]: ...   # global + per-platform
```

Per-platform block rules (examples, persisted to `compliance_rule`): `reddit_self_promo_ratio`,
`reddit_no_vote_manipulation`, `wikipedia_no_paid_self_edit`, `wikipedia_secondary_sources`,
`g2_genuine_review`, `quora_disclose_affiliation`, `listicle_sponsored_label`,
`pr_wire_factual_claims`, `expert_byline_named_author`.

### 2.5 Placement workflow (`seeding/workflow.py`) — human-in-the-loop
A state machine over `seeding_task`. Transitions to a *placed* state are **compliance-gated** and
**human-actioned** (matches ui-spec §3.5 "nothing seeds without a human click").

```
todo → briefed → compliance_review → ready_for_human → placed → corroborated
                        │  (block-severity) └──────────→ rejected
```

```python
class SeedingStatus(StrEnum):
    TODO="todo"; BRIEFED="briefed"; COMPLIANCE_REVIEW="compliance_review"
    READY_FOR_HUMAN="ready_for_human"; PLACED="placed"
    CORROBORATED="corroborated"; REJECTED="rejected"

class SeedingWorkflow:
    def __init__(self, session, tenant_id: str, engine: ComplianceEngine) -> None: ...
    def create(self, *, brand_id, channel, target_url, content_asset_id=None) -> str: ...
    def attach_brief(self, task_id: str, brief: SeedingBrief) -> None: ...       # → BRIEFED
    def run_compliance(self, task_id: str, proposal: PlacementProposal) -> ComplianceReport: ...
    def mark_placed(self, task_id: str, *, placed_url: str, actor: str) -> None: ...
    # ↑ RAISES ComplianceError unless the latest report.passed and status==READY_FOR_HUMAN
```

`run_compliance` writes the `ComplianceReport` to `seeding_task.compliance_report` and moves to
`ready_for_human` (pass) or `rejected` (block). `mark_placed` **hard-asserts** the stored report
passed — the gate cannot be bypassed programmatically.

### 2.6 Corroboration tracking (`seeding/corroboration.py`)
After placements land, measure **how many independent domains** now carry consistent brand facts
(models weight consensus — PRD §6.5). Reads citations (M1) via injected `SourceMap`/session; updates
`seeding_task.corroboration_count` and a per-brand corroboration score.

```python
def corroboration_count(session, *, tenant_id, brand_id, fact_key: str) -> int: ...
def update_corroboration(session, *, tenant_id, task_id: str) -> int: ...
```

### 2.7 Data tables (Alembic)
- `seeding_channel(id, name, source_type, tos_ruleset_ref, requires_disclosure bool, allows_ugc bool, active bool)`
  — **system-level catalog** (documented exception to per-row `tenant_id`, like `drift_event`).
- `compliance_rule(id, channel, code, description, severity, check_key, active)` — **system-level**.
- `seeding_task(id, tenant_id, brand_id, content_asset_id null, channel, target_url null, status,
  compliance_status, compliance_report jsonb, brief_ref null, placed_url null, actor null,
  corroboration_count int, created_at, updated_at)` — **tenant-scoped**.

---

## 3. Subsystem B — Self-adaptation (`src/gw_geo/orchestration/`)

Extends the M1 drift canary (`orchestration/drift.py`, `drift_event` with `retrain_flag`) into the
full **sense → adapt** loop (PRD §6.6). "Self-sensing" = monitoring + scheduled retraining +
alerting; classic MLOps, not magic.

### 3.1 Retrain trigger (`orchestration/retrain.py`)
A breached `drift_event` with `retrain_flag=True` triggers a **retrain job** for the affected
engine's ranking model (M3 `ranking/`). The trainer is an **injected `Retrainer` protocol** so M4 is
testable with a fake (no real model training, no live data pull in CI).

```python
class RetrainJob(BaseModel):
    id: str; model_engine: str; trigger_drift_event_id: str
    status: Literal["pending","running","succeeded","failed"]
    metrics_before: dict[str, float]; metrics_after: dict[str, float]
    model_ref: str | None

class Retrainer(Protocol):                          # satisfied by M3 ranking trainer
    def retrain(self, *, engine: str) -> dict[str, Any]: ...   # returns {model_ref, metrics}

class RetrainTrigger:
    def __init__(self, session, *, retrainer: Retrainer) -> None: ...
    def poll(self) -> list[RetrainJob]: ...          # scan unhandled breached drift_events
    def on_breach(self, drift_event_id: str) -> RetrainJob: ...  # idempotent per event
```

Idempotency: one `retrain_job` per `drift_event_id`; on success clears the event's `retrain_flag`.
`retrain_job` is **system-level** (engine drift is global — same exception as `drift_event`).

### 3.2 Bandit optimizer (`orchestration/bandit.py`)
Treat each **(channel, content_variant)** as a bandit arm; the **measurement/attribution signal is the
reward** (TRD §6.3/§8). Allocate finite content/seeding effort to the arms with the best measured
reward while still exploring. Interpretable policies (UCB1 default, Thompson optional).

```python
class Arm(BaseModel):
    key: str                        # f"{channel}:{variant}"
    pulls: int = 0
    reward_sum: float = 0.0
    reward_sq_sum: float = 0.0

class BanditPolicy(Protocol):
    def rank(self, arms: list[Arm]) -> list[str]: ...   # best-first arm keys

class UCB1Policy:  # score = mean + c*sqrt(2 ln(N) / n_i); unpulled arms first
    def __init__(self, c: float = 1.0) -> None: ...
    def rank(self, arms: list[Arm]) -> list[str]: ...

class ThompsonPolicy:  # Beta/Normal sampling from arm posteriors
    def __init__(self, rng=None) -> None: ...
    def rank(self, arms: list[Arm]) -> list[str]: ...

def record_reward(session, *, tenant_id, brand_id, arm_key: str, reward: float) -> None: ...
def allocate_effort(session, *, tenant_id, brand_id, budget: int,
                    policy: BanditPolicy) -> dict[str, int]: ...   # arm_key → n slots
```

Arms persist in `bandit_arm` (**tenant-scoped**). `allocate_effort` distributes `budget` placement
slots across the policy's ranking (top-weighted, with an exploration floor).

### 3.3 Continuous-loop scheduler (`orchestration/scheduler.py`)
Orchestrates one **adaptation cycle**: (1) run the M1 drift canary; (2) fire retrain triggers on
breaches; (3) refresh the citation-source map; (4) discover seeding targets; (5) bandit-allocate
effort; (6) spawn `seeding_task`s (status `todo`) for humans; (7) emit alerts (ui-spec §3.7). All
collaborators are **injected** — the scheduler is pure orchestration, unit-tested with fakes; the
real fan-out runs on an EventBridge cron → Lambda (§5).

```python
class CycleResult(BaseModel):
    drift_breaches: int; retrain_jobs: list[str]
    targets_found: int; tasks_spawned: int; alerts: list[str]

def run_adaptation_cycle(session, *, tenant_id, brand_id, since, until,
                         drift_runner, retrain_trigger: "RetrainTrigger",
                         discovery, workflow: "SeedingWorkflow", bandit_policy,
                         budget: int, date: str) -> CycleResult: ...
```

---

## 4. Subsystem C — RaaS pricing / billing (`src/gw_geo/billing/`)

The commercial layer (PRD §9, OQ4): seats + **usage** + an optional **results-linked (RaaS)**
component tied to **attributed leads/pipeline** (M2). Attribution is consumed via an injected
`AttributionSource` protocol so billing is decoupled from and testable without M2.

### 4.1 Usage metering (`billing/metering.py`)
Record and roll up billable usage. Probing dominates cost (TRD §7/§8), so metering leans on the
already-persisted `probe_run.cost_usd`, plus content generations (M3) and seeding placements (M4).

```python
class UsageKind(StrEnum):
    PROBE="probe"; GENERATION="generation"; SEEDING_PLACEMENT="seeding_placement"

def record_usage(session, *, tenant_id, brand_id, kind: UsageKind,
                 quantity: float, ts: str, source_ref: str | None = None) -> None: ...

class UsageSummary(BaseModel):
    tenant_id: str; period_start: str; period_end: str
    by_kind: dict[str, float]           # UsageKind → total quantity

def meter_period(session, *, tenant_id, period_start, period_end) -> UsageSummary: ...
```

### 4.2 Pricing model + invoice (`billing/pricing.py`)
Base fee + per-unit usage rates + optional RaaS charge on **attributed** results. RaaS basis is
either `per_lead` or `pct_pipeline` (enterprise-negotiated; PRD §9). Pure, deterministic math —
heavily unit-tested.

```python
class PricingPlan(BaseModel):
    plan: Literal["starter","growth","enterprise"]
    base_fee: float
    usage_rates: dict[str, float]       # UsageKind → $/unit
    raas_enabled: bool = False
    raas_basis: Literal["per_lead","pct_pipeline"] = "per_lead"
    raas_rate: float = 0.0              # $/lead OR fraction of pipeline

class AttributedResults(BaseModel):
    attributed_leads: int
    attributed_pipeline_usd: float

class AttributionSource(Protocol):      # satisfied by M2 attribution
    def attributed_results(self, *, tenant_id, brand_id,
                           period_start, period_end) -> AttributedResults: ...

class Invoice(BaseModel):
    tenant_id: str; period_start: str; period_end: str
    base_fee: float; usage_charges: dict[str, float]
    raas_charge: float; attributed_leads: int; attributed_pipeline_usd: float
    total: float; currency: str = "USD"

def compute_invoice(*, plan: PricingPlan, usage: UsageSummary,
                    results: AttributedResults, period_start, period_end,
                    tenant_id: str) -> Invoice: ...
```

### 4.3 Billing views (`billing/views.py`)
Read/query layer for the Settings → billing screen (ui-spec §3.8, §7 M4 RaaS/billing views):
current-period running total, usage breakdown, RaaS contribution, invoice history.

```python
def billing_summary(session, *, tenant_id, plan: PricingPlan,
                    attribution: AttributionSource, period_start, period_end) -> dict[str, Any]: ...
def invoice_history(session, *, tenant_id, limit: int = 12) -> list[dict[str, Any]]: ...
```

### 4.4 Data tables (Alembic)
- `billing_account(id, tenant_id, plan, base_fee, usage_rates jsonb, raas_enabled bool,
  raas_basis, raas_rate, currency, created_at)` — tenant-scoped.
- `usage_event(id, tenant_id, brand_id, kind, quantity, unit, ts, source_ref null)` — tenant-scoped.
- `billing_invoice(id, tenant_id, period_start, period_end, base_fee, usage_charges jsonb,
  raas_charge, attributed_leads, attributed_pipeline_usd, total, status, created_at)` — tenant-scoped.
- Self-adaptation: `retrain_job` (system-level) + `bandit_arm` (tenant-scoped) — §3.

---

## 5. Cross-cutting

- **Decoupling (mandatory):** M4 never imports M2/M3 concretely. M1 feed → `SourceMap`,
  M2 attribution → `AttributionSource`, M3 trainer → `Retrainer` are **injected protocols**; M4
  ships with in-repo fakes for CI and is buildable before those milestones land. No Gushwork /
  shared-service / cross-repo dependency anywhere (PRD §5 note, CLAUDE.md guardrail).
- **White-hat gate is a hard invariant (NG1):** the compliance engine is a *block* gate wired into
  the workflow; `mark_placed` raises unless the stored report passed. Tested that astroturf /
  hidden-text / cloaking / missing-disclosure proposals are **rejected** and cannot be forced through.
- **No live posting, ever, in tests:** placement is human-actioned; the workflow only records a
  human-supplied `placed_url`. There is **no auto-poster** and no live network in the default suite.
  Any future live path would be `@pytest.mark.live`, deselected by default (mirrors m1-design §3.3).
- **Config (`Settings`):** add `seeding_channels_enabled: list[str]`, `raas_enabled: bool = False`,
  `raas_basis: str = "per_lead"`, `raas_rate: float = 0.0`, `bandit_policy: str = "ucb1"`,
  `bandit_explore_c: float = 1.0`, `retrain_on_breach: bool = True`, `adaptation_cron` refs. Reuses
  M1 `drift_threshold`. Secrets (PR-wire API, etc.) via SSM — never in repo.
- **Multi-tenancy:** `seeding_task`, `bandit_arm`, `usage_event`, `billing_*` are tenant-scoped via
  the M0 `TenantScopedSession`. `seeding_channel`, `compliance_rule`, `retrain_job` are system-level
  (documented exceptions, same rationale as `drift_event`).
- **Alerts (ui-spec §3.7):** drift breach → retrain, new seeding opportunity, and win/corroboration
  events emit structured-log + SNS alerts, surfaced in the Alerts screen.
- **Deps:** no new heavy deps — bandit math is `math`/`statistics` (optional `scipy` already present);
  LLM briefs reuse the injected-client pattern (`httpx`/SDK), fakes in CI.
- **Conventions (unchanged):** branch `m4/T<NN>-<slug>`, TDD, hermetic tests, mypy-strict on
  `common/`, `Co-Authored-By: Claude Opus 4.8 (1M context)` trailer, per-task commit, orchestrator
  merges per wave. Everything local (no remote push). **Do not git commit while authoring these docs.**

---

## 6. Task DAG & waves (~17 tasks)

| Task | Depends on | Summary |
|---|---|---|
| M4-T01 config & secrets | M0 config | seeding/RaaS/bandit/retrain settings + flags |
| M4-T02 migrations | M0 db, M1 `drift_event` | `seeding_channel`, `compliance_rule`, `seeding_task`, `retrain_job`, `bandit_arm`, `billing_account`, `usage_event`, `billing_invoice` |
| M4-T03 compliance rules engine | M0 models | **hard white-hat gate** (global + per-platform), unit-tested |
| M4-T04 channel catalog + rule seed | T02, T03 | `seeding_channel` catalog + `compliance_rule` seed rows |
| M4-T05 target discovery | M0 models (SourceMap protocol) | citation-source map → ranked `SeedingTarget`s |
| M4-T06 per-channel briefs | T03, T05 | grounded brief + disclosure (injected `BriefLLM`) |
| M4-T07 bandit optimizer | M0 models | UCB1/Thompson policies + `Arm` math |
| M4-T08 usage metering | T02 | `record_usage` + `meter_period` |
| M4-T09 RaaS pricing/invoice | M0 models (AttributionSource protocol) | `PricingPlan` + `compute_invoice` |
| M4-T10 seeding workflow (gated) | T02, T03, T04 | state machine; compliance-gated `mark_placed` |
| M4-T11 corroboration tracking | T02, T05 | independent-domain consensus count |
| M4-T12 retrain trigger | T02, M1 drift | breach → `RetrainJob` (injected `Retrainer`) |
| M4-T13 billing views | T08, T09 | `billing_summary` + `invoice_history` |
| M4-T14 effort allocation service | T07, T10 | bandit reward from measurement/attrib → allocate slots |
| M4-T15 adaptation-cycle scheduler | T05, T10, T12, T14 | measure → sense → adapt orchestration (injected) |
| M4-T16 handlers + serverless wiring | T13, T15 | seeding/retrain/billing-close Lambdas + `serverless.yml` |
| M4-T17 M4 validation (gate + loop) | all | end-to-end: gate enforced, no live posting, cycle green |

```
Wave 0 (foundation):   T01  T02
Wave 1 (primitives):   T03 T04 T05 T06 T07 T08 T09
Wave 2 (compose):      T10 T11 T12 T13 T14
Wave 3 (integration):  T15  T16  →  T17
```
Intra-wave note (as in M1/M0): T04/T06 need T03 (Wave 1, ordered early); T14 needs T07+T10;
T15 needs T05/T10/T12/T14; T16 needs T13/T15; T17 last.

---

## 7. Testing strategy

- **Hermetic CI:** SQLite for DB; injected fakes for every external collaborator (`SourceMap`,
  `BriefLLM`, `Retrainer`, `AttributionSource`); **no live network, no live posting**. `moto` only if
  an AWS handler is exercised.
- **Compliance engine is the gate — the highest-value test surface:** unit tests assert every global
  invariant (astroturf, hidden-text, cloaking, prompt-injection, missing-disclosure) and representative
  per-platform rules **block**; a clean, disclosed proposal **passes**; and the workflow's `mark_placed`
  **raises** when the stored report failed (the gate cannot be bypassed). PRD NG1 is a tested contract.
- **Bandit:** deterministic tests — UCB1 pulls unplayed arms first then the higher-mean arm;
  `record_reward`/`allocate_effort` distribute a fixed budget correctly; Thompson tested with a
  seeded RNG.
- **Retrain trigger:** a breached `drift_event` yields exactly one `RetrainJob` (idempotent), clears
  the flag on success, and calls the injected `Retrainer` (never trains for real in CI).
- **Billing:** `compute_invoice` math property-tested (monotonic in usage/results; RaaS off ⇒ no RaaS
  charge; `per_lead` vs `pct_pipeline` both correct).
- **Scheduler:** one `run_adaptation_cycle` with fakes produces the expected `CycleResult` (breaches →
  retrain jobs, targets → spawned `todo` tasks) with no live calls.
- **Human-gated placement:** tests only ever set `placed_url` via the human-actioned method; there is
  no auto-post path to test.

---

## 8. Confirmed decisions
1. **White-hat compliance engine is a hard block gate** wired into the workflow (PRD NG1) — not advisory.
2. **All cross-milestone inputs injected as protocols** (`SourceMap`/`AttributionSource`/`Retrainer`) —
   M4 builds and tests standalone.
3. **Bandit** default **UCB1** (interpretable), Thompson optional; arms persisted per tenant.
4. **RaaS** optional (`raas_enabled=False` default; PRD OQ4), basis `per_lead` | `pct_pipeline`.
5. **No auto-poster** in v1 — seeding is workflow-assisted, human-executed (PRD §6.5).
6. **Final artifact** → `docs/tasks/M4-T*.md` + an `M4` `tasks/README` (same format as M0/M1).

## 9. Open items / risks
- Per-platform ToS changes over time → `compliance_rule` is data-driven (seeded, versioned) so rules
  update without code changes; needs periodic human review (compliance is a living dataset).
- Reward attribution latency: seeding → citation → lead takes 14–21 days (PRD §10), so bandit reward
  is delayed; allocate on a rolling window and keep an exploration floor.
- RaaS billing depends on M2 attribution quality; ship `raas_enabled=False` until attribution is
  trusted (PRD OQ4) — prove attribution first, then enable.
- PR-wire / expert-byline placements involve external vendors/credentials (out of CI, deploy-time
  secrets); the platform only produces briefs + tracks status, never auto-submits.
- Wikipedia is COI-sensitive: engine hard-blocks paid/self edits and requires verifiable secondary
  sources; treat as human-expert-only channel.
