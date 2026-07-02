# UI Spec — Dashboard (`web/`)

**Status:** Draft v1 · **Owner:** dev@gushwork.ai · **Date:** 2026-07-02
**Companion to:** [`prd.md`](prd.md) (§3 personas, §6.7 dashboard), [`trd.md`](trd.md) (§11 API).
This spec is the input to the M2 UI task breakdown (`tasks/M2-*.md`).

> The dashboard is the **end-user product**. Everything before M2 (CLI/Lambda) is internal
> plumbing that feeds it. Standalone Next.js app; talks to the backend API over HTTP only.

---

## 1. What it is

A standalone web app where a **Head of Growth / SEO** (primary persona) can: see where their
brand stands in AI answers, find and fix gaps in a click, and read the leads/pipeline it drives.
One-line promise: *"See where AI recommends you, fix the gaps in a click, and watch the revenue
it drives."*

- **Stack:** Next.js (App Router) + React + TypeScript, TanStack Query for data, Tailwind +
  shadcn/ui, Recharts for charts. Auth via a hosted provider (Clerk/Auth0) or the backend's JWT.
- **Lives in:** `web/` (sibling to the Python backend); self-contained; no shared UI libs.
- **Talks to:** the backend REST API (§6). Multi-tenant: every request carries the tenant from the
  authenticated session; the UI never sees another tenant's data.

## 2. Information architecture (navigation)

```
Sidebar
├── Overview            (visibility at a glance)
├── Visibility          (per-engine deep dive)
├── Sources             (citation-source map)
├── Opportunities       (ranked gaps → action)
├── Content             (draft → approve → publish; seeding tracker)
├── Pipeline            (leads & revenue from AI search)   ★ the payoff
├── Alerts              (drift/win notifications)
└── Settings            (brands, competitors, prompts, integrations, team/SSO)

Top bar: [Brand switcher ▾]  [Date range ▾]  [Engine filter ▾]  [account menu]
```

## 3. Screens

### 3.1 Overview
**Purpose:** the 10-second "how are we doing" read. Landing screen.
**Data:** `GET /brands/{id}/overview?range=...`
```
┌─ Overview · Acme ───────────────────────────────────────────┐
│ Share of Voice  19%  ▲2   │ AI Pipeline (Q)  $480k  ▲       │
│ Mention Rate    38%  ▲3   │ Leads from AI     137   ▲       │
├──────────────────────────────────────────────────────────────┤
│  Share-of-Voice trend (line, you vs top competitor)          │
│  ▁▂▃▃▄▅▅▆  you   ▂▂▃▃▃▃▂▂  Beta                              │
├──────────────────────────────────────────────────────────────┤
│  ⚠ 3 alerts   ·   🎯 14 open opportunities   ·   ✅ 6 wins   │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 Visibility (per-engine deep dive)
**Purpose:** where the brand stands on each engine, with confidence.
**Data:** `GET /brands/{id}/visibility?range=&geo=&persona=`
```
┌─ Visibility ────────────────────────────────────────────────┐
│ Engine            Mention   Cited   Avg Pos  Sentiment  Trend │
│ ChatGPT            42% ±6    31%      2.4       🙂        ▁▃▅  │
│ Perplexity         55% ±5    40%      1.8       🙂        ▃▅▆  │
│ Google AI Overview 12% ±7     9%      4.1       😐        ▁▁▂  │
│ Gemini             28% ±6      —      3.0       😐        ▂▃▃  │
│ Claude / Copilot / Grok / DeepSeek …                          │
├──────────────────────────────────────────────────────────────┤
│ Prompt-level table (expand a row): per-prompt mention %,      │
│ position, sample count, and a "view sampled answers" drawer.  │
└──────────────────────────────────────────────────────────────┘
```
Every metric shows its **confidence interval** and `n_samples` (non-determinism is visible, not hidden).

### 3.3 Sources (citation-source map)
**Purpose:** which sites the AIs pull from → tells you where to seed.
**Data:** `GET /brands/{id}/sources?range=`
```
┌─ Where AI cites you (and competitors) ──────────────────────┐
│ Source            Type        Cites→You   Cites→Beta         │
│ reddit.com        reddit         48%         71%   🔴 gap    │
│ g2.com            review_site    32%         55%   🔴 gap    │
│ acme.com          own_site       61%          4%             │
│ wikipedia.org     wikipedia       0%         12%   🔴 gap    │
├──────────────────────────────────────────────────────────────┤
│ Per-engine breakdown toggle. "Create opportunity" on any gap.│
└──────────────────────────────────────────────────────────────┘
```

### 3.4 Opportunities (ranked gaps → action)
**Purpose:** convert gaps into a prioritized to-do list. The bridge from insight to action.
**Data:** `GET /brands/{id}/opportunities` · `POST /opportunities/{id}/act`
```
┌─ Opportunities (ranked by est. impact) ─────────────────────┐
│ □ "best CRM for startups" — you're absent; Beta #1           │
│      via 6 Reddit threads + a G2 listicle      [ Fix this ▸ ]│
│ □ AI Overviews invisible for 9 prompts         [ Fix this ▸ ]│
│ □ Sentiment neutral on Gemini — add proof/data [ Fix this ▸ ]│
└──────────────────────────────────────────────────────────────┘
```
"Fix this" opens the **Content** flow (3.5) pre-scoped to that opportunity.

### 3.5 Content (draft → approve → publish; seeding tracker)
**Purpose:** the execution surface. Human-in-the-loop generation + off-site seeding.
**Data:** `POST /content/generate` · `POST /content/{id}/approve` · `POST /content/{id}/publish`
· `GET /seeding-tasks` · `POST /seeding-tasks/{id}/status`
```
┌─ Content workspace ─────────────────────────────────────────┐
│ [ On-site draft ]  [ Off-site seeding ]                      │
│ ── On-site ──                                                │
│  AI-generated draft (editable) · brand-voice + fact-checked  │
│  ✔ claims verified vs knowledge base   ✔ originality ok      │
│  [ Edit ]        [ Approve & Publish ▸ ]  ← human gate       │
│ ── Off-site seeding tasks ──                                 │
│  ☐ Get listed in G2 "CRM" category      status: in_progress  │
│  ☐ Answer 3 Reddit threads (briefs →)    status: todo        │
└──────────────────────────────────────────────────────────────┘
```
Guardrail badges are first-class UI; nothing publishes without explicit approval.

### 3.6 Pipeline (leads & revenue) ★ the payoff
**Purpose:** the screen that justifies the budget — revenue from AI search. No competitor has it.
**Data:** `GET /brands/{id}/pipeline?range=`
```
┌─ Revenue from AI Search · This Quarter ─────────────────────┐
│ Pipeline influenced  $480,000   │ Directly attributed $92,000│
│ Leads 137                       │ Incremental lift +23% (HO) │
├──────────────────────────────────────────────────────────────┤
│ Top converting AI answers:                                   │
│  "best CRM for SaaS startups" → 41 leads → $210k             │
│  "HubSpot alternatives"       → 28 leads → $88k              │
├──────────────────────────────────────────────────────────────┤
│ Attribution method breakdown (direct / citation-linked /     │
│ assisted / holdout-incremental) + confidence note.           │
└──────────────────────────────────────────────────────────────┘
```
Includes an **exec/board export** (PDF/CSV) and a "how this is measured" disclosure (honesty ≠ overclaim).

### 3.7 Alerts
**Purpose:** passive monitoring — drift and wins.
**Data:** `GET /brands/{id}/alerts` (also email/Slack push)
```
🔴 ChatGPT visibility −8% — likely algorithm change; re-optimizing
🟢 Now #1 recommendation for "CRM for startups"
🟡 New competitor "Gamma" appearing in 4 prompts
```

### 3.8 Settings
Brands, competitors, seed prompts (add/edit/prioritize), integrations (CRM, GA4, CMS, lead-capture
pixel install snippet), team & roles (RBAC), SSO (SAML/OIDC), billing/plan.

## 4. Cross-cutting UX rules
- **Confidence is always visible** — every rate shows CI + sample size (builds trust; matches TRD §3).
- **Empty/first-run states** — onboarding wizard (brand → competitors → integrations → seed topics →
  "measuring… check back" state) until the first snapshot lands.
- **Approval gates are explicit** — no content publishes or seeds without a human click.
- **Loading = skeletons**, not spinners; all lists paginated/virtualized; all views date-range + engine filterable.
- **Multi-brand switcher** in the top bar (agency/multi-brand accounts).

## 5. Auth & tenancy
Session (Clerk/Auth0 or backend JWT) → `tenant_id` + role on every request. Roles: `owner`,
`admin`, `editor` (can approve/publish), `viewer`. No client-side tenant selection; server enforces scope.

## 6. API contract (what the dashboard needs from the backend)

REST, JSON, bearer-authed, tenant derived from the token. Read endpoints back the dashboard;
write endpoints drive execution. (Backend: TRD §11; feed queries: `m1-design.md` §5.)

| Method & path | Purpose | Returns (shape) |
|---|---|---|
| `GET /brands` | list brands for tenant | `[{id,name,domain,competitors[]}]` |
| `POST /brands` | create/onboard brand | `{id}` |
| `GET /brands/{id}/overview?range` | 3.1 KPIs + SoV trend | `{sov,mention_rate,pipeline,leads,trend[]}` |
| `GET /brands/{id}/visibility?range&geo&persona` | 3.2 per-engine + prompt-level | `{engines:[{engine,mention_rate,ci,cited,avg_position,sentiment,trend[]}],prompts:[…]}` |
| `GET /brands/{id}/sources?range` | 3.3 citation-source map | `[{domain,source_type,you_pct,competitor_pcts}]` |
| `GET /brands/{id}/opportunities` | 3.4 ranked gaps | `[{id,title,rationale,est_impact,engine}]` |
| `POST /opportunities/{id}/act` | spawn content/seeding from a gap | `{content_id \| seeding_task_id}` |
| `POST /content/generate` | draft for a prompt/opportunity | `{content_id,draft,guardrails:{claims_ok,originality_ok}}` |
| `POST /content/{id}/approve` · `/publish` | human gate → publish | `{status,published_url?}` |
| `GET /seeding-tasks` · `POST /seeding-tasks/{id}/status` | off-site tracker | `[{id,channel,status}]` |
| `GET /brands/{id}/pipeline?range` | 3.6 revenue view | `{influenced,attributed,leads,lift,top_answers[],method_breakdown}` |
| `GET /brands/{id}/alerts` | 3.7 alerts | `[{severity,message,ts}]` |
| `GET/POST /brands/{id}/prompts` | manage prompt set | `[{id,text,intent_cluster,geo,persona}]` |
| `POST /integrations/{kind}` | connect CRM/GA4/CMS | `{status}` |
| `GET /lead-capture/snippet` | install pixel/SDK snippet | `{snippet}` |

Realtime (optional M3+): SSE/WebSocket for "measuring…" progress and live alert toasts.

## 7. Milestone mapping
- **M2:** Overview, Visibility, Sources, Pipeline, Alerts, Settings + onboarding + auth/tenancy +
  the read API + CRM/GA4 integrations + lead-capture pixel. *(Dashboard goes live.)*
- **M3:** Opportunities + Content (on-site) workspace + generate/approve/publish API.
- **M4:** Off-site seeding tracker + RaaS/billing views + self-adaptation surfacing in Alerts.

## 8. Open items
- Auth provider choice (Clerk vs Auth0 vs self-hosted JWT) — align with TRD §2.
- Design system: shadcn/ui default vs a custom theme/brand (tied to product-naming OQ3).
- Agency white-label depth (custom domains/logos) — defer unless an agency buyer lands early.
