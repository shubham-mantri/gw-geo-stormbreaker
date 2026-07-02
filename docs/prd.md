# PRD — Stormbreaker: AI-Search Visibility, Attribution & Execution Platform

**Status:** Draft v1 · **Owner:** dev@gushwork.ai · **Last updated:** 2026-07-02
**Repo:** `gw-geo-stormbreaker` (working name) · **Product name:** TBD
**Note:** A standalone, self-contained product. It does not depend on or integrate with any other
codebase — its own stack, auth, storage, and deploy. Product/GTM branding is an open item (§14, OQ3).

---

## 0. TL;DR

Stormbreaker is a standalone, enterprise-grade platform that does three things no competitor does end-to-end:

1. **Measures** how brands appear in AI answer engines (ChatGPT, Gemini, Perplexity, Google AI Overviews/AI Mode, Copilot, Claude, Grok, DeepSeek) — built in-house.
2. **Executes** — generates, publishes, and seeds (on-site + off-site) the content that earns citations.
3. **Attributes** — ties AI-search citations → site visitors → captured leads → pipeline/revenue.

The incumbents (Profound, Athena, Scrunch) own #1 and stop there. Stormbreaker's wedge is closing the loop to **#3 (revenue), backed by #2 (real execution)** — reframing the category from a vanity dashboard into a measurable revenue channel.

**v1 includes all three pillars**, sequenced into five milestones (M0–M4). Enterprise self-serve is the primary buyer.

---

## 1. Problem & Opportunity

### 1.1 The shift
Buyers increasingly research and shortlist vendors by asking AI assistants ("what's the best X for Y?") before ever touching a search engine or a website. If the model doesn't name and recommend your brand, you're invisible at the decision moment. Traditional SEO does not control this surface.

### 1.2 What's broken with today's tools
- **Measurement-only.** Profound (now a $1B unicorn), Athena, and Scrunch tell a brand *"you're mentioned 14% of the time"* and hand over a recommendations PDF. They don't do the work or prove the money.
- **No revenue attribution.** None connect AI-search visibility to captured leads or pipeline. In buyers' words, "value gets relitigated every quarter." This is the category's biggest unsolved problem.
- **Weak execution.** Athena's generated content has documented plagiarism/accuracy problems; Scrunch writes nothing (infra-only); Profound's agents are a maturing 2026 bolt-on that still depends on the customer's own CMS.
- **Off-site blind spot.** LLMs cite Reddit, G2, Wikipedia, news, comparison sites — the tools *report* which sources matter but don't *place* content there.

### 1.3 Why this wins
The wedge is owning the **whole loop** incumbents refuse to: not just measurement, but the
**execution** (generate → publish → seed the content that earns citations) and the
**attribution** (citation → visitor → captured lead → pipeline) on top of it. This product builds
all three in-house — its own measurement system of record, its own content/publishing engine, its
own lead-capture + attribution. The compounding moat is the proprietary dataset it accumulates:
**content → citation → lead**, which no measurement-only competitor can assemble because none of
them ever see the lead.

### 1.4 Market reality check (honest)
AI-search referrals are still a small share of total traffic today. The attribution pillar is a deliberate hedge: it is the instrument that will *prove* when this channel crosses the ROI threshold — and we will own that data before anyone else. We design for the world where this share compounds, while giving buyers a defensible reason to pay now (measured leads, not vanity metrics).

---

## 2. Goals & Non-Goals

### 2.1 Goals (v1)
- G1. In-house measurement across ≥8 engines with statistically sound handling of answer non-determinism.
- G2. Attribution: link AI-answer citations to on-site sessions and captured leads with a defensible (if probabilistic) methodology.
- G3. Execution: generate on-brand, factually-grounded content; publish it; seed high-authority off-site sources — with a human approval gate.
- G4. Self-adapting optimization: detect engine/algorithm drift and re-learn what earns citations.
- G5. Enterprise-ready: multi-tenant, RBAC, SSO (SAML/OIDC), SOC 2 Type II path, audit logs.
- G6. A results-linked commercial model (seats + usage + optional performance component).

### 2.2 Non-Goals (v1)
- NG1. Grey-hat tactics (hidden-text prompt injection, cloaking, serving divergent content to bots). Explicitly out — white-hat only; it's a brand-safety liability for enterprise clients and a platform-policy risk.
- NG2. A licensed consumer-conversation panel at Profound's scale (prompt-volume estimation is v2; we approximate in v1 — see §6.1).
- NG3. Full e-commerce/Shopping product-feed optimization (v2).
- NG4. Paid-ads management / DSP — out of scope; this product is organic AI-search only.

---

## 3. Users & Personas

**Primary (enterprise self-serve):**
- **Head of SEO / Organic Growth** — owns the channel, wants share-of-voice + a way to prove pipeline impact to the CMO.
- **Content/Brand lead** — needs the execution engine (briefs → drafts → publish) and brand-safety controls.
- **Demand-gen / RevOps** — cares only about attribution: leads and pipeline from AI search.
- **Agency operator** — manages many client brands; needs multi-brand workspaces and white-label reporting.

**Secondary:** CMO/VP Marketing (board-ready reporting, ROI), PR/Comms (sentiment, hallucination/brand-integrity alerts).

**Buying center:** Head of Growth champions; CMO approves budget; Security/IT gates on SSO + SOC 2.

---

## 4. Competitive Positioning

| Capability | Profound | Athena | Scrunch | **Stormbreaker** |
|---|---|---|---|---|
| Multi-engine measurement | ✅ (best-in-class) | ✅ | ✅ | ✅ (in-house) |
| Prompt-volume intelligence | ✅ (moat: real panel) | ~ (QVEM) | ❌ | ~ v1 approx → v2 |
| Content generation | ~ (new) | ⚠️ (quality issues) | ❌ | ✅ (grounded, gated) |
| Off-site seeding (done-for-you) | ❌ | ❌ | ❌ | ✅ |
| Serve-to-bots infra | ❌ | ❌ | ✅ (AXP) | ❌ (out of scope) |
| **Revenue attribution** | ❌ | ❌ | ❌ | ✅ **(wedge)** |
| Results-linked pricing | ❌ | ❌ | ❌ | ✅ optional |

**Positioning statement:** *"Every other AI-search tool tells you how visible you are. Stormbreaker makes you visible — and shows you the revenue."*

**Don't fight where they're strong:** we do not try to out-build Profound's consumer-conversation panel in v1, and we don't build Scrunch's edge-serving AXP. We win on the closed loop (execution + attribution) they structurally can't match without becoming a services company.

---

## 5. System Architecture (overview)

Seven subsystems around a shared data platform. The core loop mirrors the proven GEO pattern: **Discover → Recognize → Rank → Generate → Seed → Attribute → Re-learn.**

```
                          ┌───────────────────────────────────────────┐
                          │            DATA PLATFORM (multi-tenant)     │
                          │  Postgres · object store · vector DB · queue│
                          └───────────────────────────────────────────┘
   (1) MEASUREMENT        (2) ATTRIBUTION        (3) FEATURE/RANK ML
   Discover→Probe→Parse   citation→visitor→lead  what content gets cited
        │                        ▲                       │
        ▼                        │                       ▼
   (4) CONTENT ENGINE  ───────────────────────────►  (5) OFF-SITE SEEDING
   generate · ground · gate · publish               place on cited sources
        │                                                │
        └──────────────► (6) ORCHESTRATION / AGENTS ◄────┘
                          plan work, run the loop, retrain
                                     │
                                     ▼
                          (7) DASHBOARD / API / REPORTING
                          visibility · pipeline · alerts · exec view
```

---

## 6. Subsystem Requirements (detailed)

### 6.1 Measurement Harness (built in-house) — *system of record*

**Purpose:** Given a brand + competitors + prompt set, quantify presence in AI answers over time.

**Pipeline:**
1. **Discover (prompt universe).** Build 500–5,000 representative prompts per brand from: seed topics, People-Also-Ask, Reddit/Quora/forum mining, the client's own search/query logs, sales-call transcripts, and LLM paraphrase expansion. Cluster by buyer-intent stage. *v1 prompt-volume approximation:* weight prompts by traditional search volume + retrieval frequency as a proxy (true panel-based volume is v2).
2. **Probe (execute at scale).** For each `(prompt, engine, geo, persona)`, capture the full answer + cited sources. Hybrid capture: **official APIs where they exist** (Gemini, Perplexity Sonar, Bing/Copilot, Claude, DeepSeek); **headless-browser automation** for consumer-only surfaces (ChatGPT consumer, Google AI Overviews/AI Mode, Grok). Must monitor the **consumer-facing product with live retrieval**, not just static model APIs.
3. **Parse (extract signals).** From each answer: brand mentioned? cited as source? rank/position in answer? sentiment? which URLs/domains cited? competitor set present? Store raw answer + structured extraction.
4. **Sample & aggregate (non-determinism — critical).** Same prompt → different answers by user/geo/session/temperature. **Never treat a single response as truth.** Sample N times per `(prompt, engine, geo)` (configurable; default N=5–10), across rotating accounts/regions, and report **probabilities** ("brand appears in 42% ± CI of runs"). Publish confidence intervals. Track and surface **citation drift** over time.
5. **Refresh cadence.** Daily for high-value/new prompts; every 3 days baseline; on-demand trigger. Continuous **canary set** for drift detection (feeds §6.6).

**Infra requirements:** rotating proxy/residential IP pool + managed account fleet for consumer-surface probing; robust anti-bot handling; per-engine adapter abstraction (engines change often — isolate each behind a stable interface); rate-limit + cost governor (probing is the dominant cost driver, not generation).

**Metrics produced:** Visibility/Share-of-Voice, Mention Rate, Citation Rate, Position, Sentiment, Citation-Source Authority map (which domains each engine trusts in this category), per engine/geo/persona.

**Key design note:** each engine has distinct source preferences (e.g., Perplexity → Reddit/backlinks; Google AI Overviews → your own indexed pages; ChatGPT → info-dense authoritative pages). The harness must tag citations by source-type so §6.3 and §6.5 can act per-engine.

### 6.2 Attribution Engine — *the wedge*

**Purpose:** Connect AI-search presence to real business outcomes. This is the differentiator; it must be defensible even though perfect causal attribution is impossible.

**Mechanisms (layered, strongest to weakest):**
1. **Direct referral capture.** Tag and detect sessions arriving from AI engines (referrer/UTM where available, e.g. `chatgpt.com`, `perplexity.ai`, `gemini.google.com`). Lands the visitor in the product's own lead-capture (tracking pixel/SDK on client pages); record `answer_engine → landing_page → lead → CRM stage → $`.
2. **Citation-to-page linkage.** When the harness sees our seeded page/URL cited for a prompt, and that same URL then receives AI-referred sessions, link them.
3. **Assisted/last-non-direct modeling.** For buyers who see the brand in an AI answer then arrive later via branded search/direct, use a probabilistic assist model (surveys "how did you hear about us", branded-search lift correlated to visibility gains, holdout geos).
4. **Holdout experiments.** For a subset of prompts/topics, deliberately do not optimize; compare lead flow vs optimized cohort to estimate incremental lift. This is the credibility backbone — sell *incrementality*, not vanity.

**Output:** a pipeline/revenue view: "AI search influenced $X pipeline this quarter; $Y directly attributed; Z leads; top-converting prompts/answers." Board-ready.

**Integrations:** CRM (HubSpot, Salesforce), GA4, the product's own lead-capture pixel/SDK, form/webhook ingestion, offline-conversion upload.

### 6.3 Feature/Rank ML — *what earns citations*

**Purpose:** Learn, per engine, which content characteristics predict being cited, and turn that into actionable levers for §6.4/§6.5.

**Approach:** Start interpretable, not black-box.
- **Labels** from §6.1: pages/sources that got cited vs didn't, per prompt/engine.
- **Features:** structure (definition-first opening, FAQ/schema, tables, listicle), info density (stats per 100 words), freshness signals, source-domain authority, multi-source corroboration count, embedding similarity to prompt intent, entity/claim consistency across sources.
- **Model:** gradient-boosted trees / logistic regression for explainability ("schema + ≥3 stats/para + cited on ≥5 domains → +X citation probability"); embeddings for semantic-match feature. Per-engine models (Doubao≠DeepSeek≠Perplexity in what they trust — same principle applies to Western engines).
- **Output:** ranked "feature factors" + content gaps + channel recommendations ("Perplexity pulls from Reddit here → seed Reddit"). Treat generation/placement as a **bandit**: each (content-variant, channel) is an arm; the harness is the reward signal.

*The moat: a proprietary, longitudinal dataset mapping content → citation → lead, accumulated across every client. No competitor sees the lead, so no competitor can build this.*

### 6.4 Content Engine — *execution, on-site*

**Purpose:** Generate on-brand, factually-grounded content conditioned on §6.3's winning features, and publish it.

**Requirements:**
- **Grounding & brand knowledge base** per client (approved facts, USPs, products, claims, pricing, certifications) — the source of truth for generation. Prevents the hallucination/plagiarism failures that plague Athena.
- **Conditioned generation:** LLM (Claude/GPT) produces content shaped to the target engine's learned feature profile + intent cluster; formats optimized for extraction (direct-answer blocks, FAQ/HowTo schema/JSON-LD, comparison tables, quantified stats).
- **Guardrails:** originality/plagiarism check, claim-verification against the knowledge base, brand-voice conformance, no fabricated stats. **Human approval gate** before publish (enterprise requirement).
- **Publishing:** to client CMS via connectors (WordPress, Webflow, Framer, headless/API) or a product-hosted knowledge-base subdomain; freshness metadata (datePublished/dateModified), sitemap resubmission.
- Built in-repo: LLM generation + a publishing connector layer, wrapped with the grounding/guardrail/gate.

### 6.5 Off-Site Seeding — *execution, off-site (nobody else does this)*

**Purpose:** Place content on the third-party sources LLMs actually cite.

**Requirements:**
- **Target discovery:** from §6.1's citation-source map, identify the high-authority domains/communities each engine trusts for the client's prompts (Reddit subs, Quora, G2/Capterra, industry listicles, Wikipedia-eligible facts, PR/wire, expert bylines, YouTube).
- **Placement workflows:** content briefs + human-in-the-loop outreach/publishing per channel; wire-service PR distribution; review/listicle inclusion; UGC participation that complies with each platform's rules.
- **Compliance guardrails:** strictly white-hat; per-platform ToS rules engine; disclosure where required. **No manipulation/astroturfing** — reputationally and legally out.
- **Corroboration tracking:** measure how many independent domains now carry consistent brand facts (models weight consensus).

*This is deliberately service-heavy in v1 (workflow-assisted, human-executed). Automating it is a later phase; the differentiation is that we do it at all.*

### 6.6 Orchestration / Agents & Self-Adaptation

**Purpose:** Run the loop and keep it working as engines change.
- **Workflow engine** schedules probes, triggers generation/seeding against ranked opportunities, routes approvals.
- **Drift detection:** continuous canary prompts; when citation rates for known-good patterns drop sharply → alert + trigger re-measurement + retrain §6.3. (This is "self-sensing" = monitoring + scheduled retraining + alerting; classic MLOps, not magic.)
- **Agent layer** (in-repo) runs the repeatable steps (research, draft, gap-analysis) via an LLM agent loop.

### 6.7 Dashboard / API / Reporting

- **Visibility views:** SoV, mention/citation/position/sentiment by engine/geo/persona/competitor; citation-source maps; drift trends with confidence intervals.
- **Pipeline view (headline):** leads & pipeline from AI search; incrementality from holdouts; top-converting prompts/answers.
- **Opportunity queue:** ranked gaps → one-click spawn generation/seeding tasks.
- **Brand-integrity alerts:** hallucination/misinfo detection, sentiment crisis alerts.
- **Executive/board reporting** + BI export (Looker/Tableau/Power BI), scheduled reports, white-label for agencies.
- **Public API + webhooks**; MCP connector (let a client's own LLM query Stormbreaker data).

---

## 7. Data Model (core entities)

- `tenant`, `user`, `role`, `brand`, `competitor`
- `prompt` (text, intent_cluster, geo, persona, volume_estimate)
- `probe_run` (prompt_id, engine, geo, persona, ts, raw_answer, cost)
- `answer_extraction` (probe_run_id, brand_mentioned, position, sentiment, cited_urls[])
- `citation` (url, domain, source_type, engine, prompt_id, first_seen, last_seen)
- `visibility_snapshot` (brand_id, engine, date, sov, mention_rate, citation_rate, ci)
- `content_asset` (brand_id, type[onsite|offsite], target_engine, features, status, published_url)
- `seeding_task` (content_asset_id, channel, status, compliance_check)
- `session` / `lead` / `attribution_link` (citation_id ↔ session_id ↔ lead_id ↔ crm_stage ↔ value)
- `feature_model` / `drift_event` / `holdout_cohort`

---

## 8. Non-Functional Requirements

- **Scale:** millions of probes/day across engines; design cost governor + sampling budget per tenant from day one (probing cost dominates).
- **Multi-tenancy & isolation:** hard tenant boundaries; per-tenant data ownership.
- **Security/compliance:** RBAC, SSO (SAML/OIDC), audit logs, SOC 2 Type II roadmap, GDPR/data-processing terms, PII handling for lead data.
- **Reliability:** per-engine adapter isolation so one engine's change/outage doesn't break the platform; graceful degradation; observability on capture success rates.
- **Extensibility:** adding a new engine = writing one adapter, not touching the core.

---

## 9. Pricing & Packaging (v1 hypothesis)

- **Self-serve tiers** (compete with Profound/Athena on entry): Starter / Growth / Enterprise (custom), scaling engines, prompts, seats, brands.
- **Execution add-on:** content generation + seeding priced by volume (this is the do-the-work premium).
- **Performance/RaaS option (differentiator):** a component tied to attributed leads/pipeline — credible *because* we have attribution. Optional, enterprise-negotiated.
- Free **AI Visibility Report** as the lead magnet (every competitor uses this; it works).

---

## 10. Success Metrics

**Product/North Star:** *Attributed pipeline influenced by AI search, per customer.* (No competitor can report this — make it the headline.)

Supporting:
- Measurement: capture success rate per engine ≥ target; drift caught before customer notices.
- Execution: time-to-first-citation for a new optimized asset (target 14–21 days); % assets earning ≥1 citation.
- Attribution: % of AI-referred leads captured & linked; incrementality lift from holdouts.
- Business: logo count, NRR, execution-attach rate, RaaS-attach rate.

---

## 11. Phased Roadmap (v1 arc → M0–M4)

Even with "all pillars in v1," subsystems must be sequenced:

- **M0 — Foundations (data platform, multi-tenant skeleton, 1 engine adapter, dashboard shell).** Prove capture + storage on ChatGPT + Perplexity.
- **M1 — Measurement GA.** ≥8 engine adapters, sampling/aggregation with CIs, SoV/citation/sentiment views, citation-source map, drift canary. *Shippable as a standalone visibility product — early revenue.*
- **M2 — Attribution.** Referral capture + citation-to-page linkage + CRM/GA4 integration + holdout framework. Pipeline view live. *The wedge goes live.*
- **M3 — Execution (on-site).** Brand knowledge base, grounded generation, guardrails, approval gate, publishing connectors. Feature/Rank ML v1 feeding recommendations.
- **M4 — Execution (off-site) + Self-adaptation.** Seeding workflows + compliance engine; drift-triggered retraining; bandit optimization; RaaS pricing pilot.

Enterprise-readiness (SSO, RBAC, SOC 2 path, audit logs) is threaded through M1–M4, not a separate phase.

---

## 12. Team & Resourcing (indicative)

- **Measurement/infra eng (2):** the harness, adapters, proxy/account fleet, cost governor. Hardest, highest-priority.
- **Data/ML eng (1–2):** feature model, aggregation stats, attribution modeling, drift/retraining.
- **Full-stack/product eng (2):** dashboard, API, integrations, multi-tenancy.
- **Applied content/AEO strategist (1):** ground truth, guardrails, seeding playbooks, compliance rules.
- **Frontend eng (1).** The Next.js dashboard (see `ui-spec.md`), from M2.
- **PM + design (1 each).**

---

## 13. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Attribution is inherently fuzzy; buyers distrust it | Lead with **incrementality/holdouts**, publish confidence intervals, never overclaim causation |
| Engines block/scrape-defend consumer surfaces | Adapter isolation, proxy/account fleet, API-first where possible, degrade gracefully |
| Probing cost explodes | Per-tenant sampling budget + cost governor from M0; smart prompt prioritization |
| Content quality/hallucination (Athena's failure) | Knowledge-base grounding + claim verification + human approval gate |
| Off-site seeding drifts into grey-hat/astroturfing | Hard white-hat guardrails, per-platform ToS engine, disclosure; NG1 |
| Category ROI still unproven (<5% traffic) | Attribution *is* the instrument that proves timing; sell measured value, not hype |
| Incumbents (esp. $1B Profound) move into execution/attribution | Speed + focus on the closed loop; own the content→citation→lead data flywheel they can't assemble (they never see the lead) |

---

## 14. Open Questions

- OQ1. Build the measurement account/proxy fleet in-house vs. managed vendor for *just the capture infra* (not the analytics)? (User chose in-house measurement — confirm this extends to the raw capture infra.)
- OQ2. Which CRM to integrate first (HubSpot vs Salesforce) — driven by target-customer profile.
- OQ3. Product brand/domain name (currently working name `gw-geo-stormbreaker`). Affects GTM.
- OQ4. RaaS component in v1 pricing, or prove attribution first then introduce it?
- OQ5. Geographic/engine priority — Western engines only in v1, or include DeepSeek/Doubao for APAC clients?

---

*End of PRD v1 draft.*
