# M2 Design — Product GA (Attribution + API + Dashboard)

**Design spec for Milestone 2** · **Status:** Draft v1 · **Owner:** dev@gushwork.ai · **Date:** 2026-07-02
**Companion to:** [`prd.md`](prd.md) (§3 personas, §6.2 attribution, §6.7 dashboard),
[`trd.md`](trd.md) (§6 attribution, §7 multi-tenancy/cost, §11 API surface),
[`ui-spec.md`](ui-spec.md) (the 8 screens + the REST contract §6 + milestone map §7), and
[`m1-design.md`](m1-design.md) §5 (the feed query layer M2 consumes). This spec is the input to the
M2 task breakdown (`tasks/M2-*.md`).

---

## 1. Goal (definition of done)

**Product GA — the end-user product ships.** M2 turns the internal M0/M1 measurement pipeline into a
sellable product: an **attribution engine** (4 layered mechanisms), a **REST API layer** that serves
the [`ui-spec.md`](ui-spec.md) §6 contract verbatim, a live **`web/` Next.js dashboard** (Overview,
Visibility, Sources, Pipeline, Alerts, Settings + onboarding), **CRM/GA4 integrations**, a
**lead-capture pixel/SDK**, and **auth + RBAC + multi-tenancy** enforced server-side on every
request. After M2 a Head of Growth can log in, onboard a brand, watch visibility, and read the
leads/pipeline AI search drives — with confidence intervals and an honest attribution-method
breakdown.

**M2 adds zero changes to the M0/M1 core contracts** (`EngineAdapter`, `ProbeResult`/models,
`run_measurement`, the `measurement/feed.py` query layer from M1). The API layer is a thin,
tenant-scoped read/write skin over the existing feed queries plus the new `attribution/` package.
Attribution is **additive and edge-attached**: it reads `citation` (M0) + new `session`/`lead` rows
and never mutates the measurement pipeline.

**Scope boundary (from ui-spec §7):** M2 ships **Overview, Visibility, Sources, Pipeline, Alerts,
Settings + onboarding**. Opportunities + Content (generate/approve/publish) + Seeding are **M3/M4**
and are explicitly out of scope — those endpoints are not built here.

**Auth decision:** self-contained **backend JWT** (email/password + argon2 hashing, short-lived
access token carrying `tenant_id` + `role`, refresh token). No Clerk/Auth0 dependency — keeps the
project standalone (TRD §2). SSO (SAML/OIDC) is a Settings stub in M2, wired in a later milestone.

**Non-overclaim rule (credibility backbone):** every pipeline number exposes a
`method_breakdown` (direct / citation-linked / assisted / holdout-incremental) and a confidence
note. **Holdout incrementality is the only causal claim we make**; the other three are correlational
and labelled as such. The UI never presents a single attribution number without its method mix
(PRD §13 risk, ui-spec §3.6).

---

## 2. Subsystem A — Attribution engine (`src/gw_geo/attribution/`)

Four layered mechanisms, strongest→weakest (TRD §6, PRD §6.2). Each is a pure, injectable,
tenant-scoped function/class over `session`/`lead`/`citation` rows; none call live services in tests.

### 2.1 Ingestion substrate (`attribution/ingest.py`)
The lead-capture pixel (§6) beacons land here. Writes `session` + `lead` rows.

```python
class SessionEvent(BaseModel):
    tenant_id: str; brand_id: str
    visitor_id: str                       # first-party cookie id from the pixel
    landing_url: str; referrer: str | None
    utm: dict[str, str] = Field(default_factory=dict)
    user_agent: str | None = None; ts: datetime

class LeadEvent(BaseModel):
    tenant_id: str; brand_id: str; visitor_id: str
    email: str | None; value_usd: float | None = None
    crm_stage: str | None = None; self_reported_source: str | None = None; ts: datetime

def ingest_session(session, ev: SessionEvent) -> str: ...   # -> session.id, tenant-scoped
def ingest_lead(session, ev: LeadEvent) -> str: ...         # -> lead.id, links to latest session
```

### 2.2 Mechanism 1 — Direct referral capture (`attribution/referral.py`) *(strongest)*
Detect sessions arriving **from an AI engine** by referrer/UTM. A static, versioned map of engine
referrer hosts → engine name. On match, write an `attribution_link(method="direct", confidence=high)`.

```python
AI_ENGINE_REFERRERS: dict[str, str] = {
    "chatgpt.com": "chatgpt", "chat.openai.com": "chatgpt",
    "perplexity.ai": "perplexity", "www.perplexity.ai": "perplexity",
    "gemini.google.com": "gemini", "copilot.microsoft.com": "copilot",
    "claude.ai": "claude", "grok.com": "grok", "x.com": "grok",
}
def classify_referrer(referrer: str | None, utm: dict[str, str]) -> str | None: ...  # -> engine|None
def link_direct(session, *, tenant_id: str, brand_id: str,
                since: str, until: str) -> list[AttributionLink]: ...
```

### 2.3 Mechanism 2 — Citation-to-page linkage (`attribution/linkage.py`)
Join AI-referred sessions to the **specific seeded page** the AI cited: match `session.landing_url`
(normalized) to a `citation.url` for the same brand/engine. Upgrades a direct link with the
citation id (which answer/prompt drove it) → `attribution_link(method="citation_linked")`.

```python
def link_citations(session, *, tenant_id: str, brand_id: str,
                   since: str, until: str) -> list[AttributionLink]: ...
# matches normalized landing_url ↔ citation.url (reuses M0 url-normalization), records citation_id + prompt_id
```

### 2.4 Mechanism 3 — Assisted modeling (`attribution/assisted.py`) *(correlational)*
For buyers who saw the brand in an AI answer then arrived later via branded search/direct:
(a) ingest self-reported "how did you hear about us" (`lead.self_reported_source`), and
(b) correlate branded-search lift to visibility gains. Produces **probabilistic** assisted credit,
always flagged low-confidence.

```python
def assisted_credit(session, *, tenant_id: str, brand_id: str, since: str, until: str,
                    visibility_series: list[dict]) -> list[AttributionLink]: ...
# self-report → method="assisted", confidence="reported"
# branded-lift correlation → method="assisted", confidence="modeled" (never causal)
```

### 2.5 Mechanism 4 — Holdout incrementality (`attribution/holdout.py`) *(the causal backbone)*
Compare lead flow in an **un-optimized holdout cohort** (prompts/geos deliberately not optimized) vs
an optimized cohort → estimate incremental lift with a CI. This is the **only causal claim**.

```python
class HoldoutResult(BaseModel):
    cohort_id: str; holdout_leads: int; optimized_leads: int
    lift_pct: float; ci_low: float; ci_high: float; n_holdout: int; n_optimized: int; significant: bool

def measure_incrementality(session, *, tenant_id: str, brand_id: str,
                           cohort_id: str, since: str, until: str) -> HoldoutResult: ...
# two-proportion lift + Wilson/bootstrap CI (reuse M0 stats conventions, TRD §3)
```

### 2.6 Pipeline aggregation (`attribution/pipeline.py`)
The one function the `/pipeline` endpoint calls. Composes all four mechanisms into the ui-spec §3.6
shape, with the **method breakdown + confidence note** front and centre.

```python
def pipeline_view(session, *, tenant_id: str, brand_id: str, since: str, until: str) -> dict:
    # returns: {influenced, attributed, leads, lift,
    #           top_answers:[{prompt, leads, value}],
    #           method_breakdown:{direct, citation_linked, assisted, holdout_incremental},
    #           confidence_note}
```

---

## 3. Subsystem B — REST API layer (`src/gw_geo/api/`)

**FastAPI** (async, Pydantic-native, OpenAPI for free, `Mangum`-wrappable onto the existing Lambda
target). Serves the [`ui-spec.md`](ui-spec.md) §6 contract **exactly** — path, query params, and
response JSON shapes are binding. Every route resolves `tenant_id` + `role` from the JWT and runs
through a `TenantScopedSession` (TRD §7); **no endpoint accepts a client-supplied tenant**.

```
api/app.py          # create_app() -> FastAPI; router mounting, CORS for web/, error handlers
api/deps.py         # get_current_principal(), require_role(...), scoped_session() dependencies
api/auth.py         # JWT issue/verify, argon2 hashing, login/refresh routes
api/schemas.py      # response models matching ui-spec §6 shapes 1:1
api/routers/
  brands.py         # GET/POST /brands, GET /brands/{id}/overview
  visibility.py     # GET /brands/{id}/visibility, GET /brands/{id}/sources
  pipeline.py       # GET /brands/{id}/pipeline, GET /brands/{id}/alerts
  settings.py       # GET/POST /brands/{id}/prompts, POST /integrations/{kind}, GET /lead-capture/snippet
  leadcapture.py    # POST /lead-capture/collect  (public pixel beacon, write-key auth)
```

**Contract (M2 subset of ui-spec §6):**

| Method & path | Backing service | Returns |
|---|---|---|
| `POST /auth/login` · `/auth/refresh` | `api/auth.py` | `{access_token, refresh_token, role, tenant_id}` |
| `GET /brands` | `TenantScopedSession.query_brands` | `[{id,name,domain,competitors[]}]` |
| `POST /brands` | onboarding | `{id}` |
| `GET /brands/{id}/overview?range` | `feed.share_of_voice_trend` + `pipeline_view` | `{sov,mention_rate,pipeline,leads,trend[]}` |
| `GET /brands/{id}/visibility?range&geo&persona` | `feed.visibility_timeseries` | `{engines:[{engine,mention_rate,ci,cited,avg_position,sentiment,trend[]}],prompts:[…]}` |
| `GET /brands/{id}/sources?range` | `feed.citation_source_mix` | `[{domain,source_type,you_pct,competitor_pcts}]` |
| `GET /brands/{id}/pipeline?range` | `attribution.pipeline_view` | `{influenced,attributed,leads,lift,top_answers[],method_breakdown}` |
| `GET /brands/{id}/alerts` | `alerts` (drift_event + wins) | `[{severity,message,ts}]` |
| `GET/POST /brands/{id}/prompts` | prompt CRUD | `[{id,text,intent_cluster,geo,persona}]` |
| `POST /integrations/{kind}` | CRM/GA4 connect | `{status}` |
| `GET /lead-capture/snippet` | pixel installer | `{snippet}` |
| `POST /lead-capture/collect` | `attribution.ingest_*` | `202 {ok:true}` |

Deferred to M3/M4 (documented, not built): `/opportunities`, `/content/*`, `/seeding-tasks`.

---

## 4. Subsystem C — `web/` Next.js dashboard

Standalone Next.js (App Router) + TypeScript app; **HTTP-only** to the API (§3). Talks to no sibling
service. Stack per ui-spec §1: TanStack Query, Tailwind + shadcn/ui, Recharts, Playwright + Vitest/RTL
for tests.

```
web/
  app/(auth)/login/page.tsx
  app/(app)/layout.tsx                 # sidebar + top bar (brand switcher, date range, engine filter)
  app/(app)/overview/page.tsx          # 3.1
  app/(app)/visibility/page.tsx        # 3.2
  app/(app)/sources/page.tsx           # 3.3
  app/(app)/pipeline/page.tsx          # 3.6  ★ payoff
  app/(app)/alerts/page.tsx            # 3.7
  app/(app)/settings/page.tsx          # 3.8 (brands, prompts, integrations, team/SSO stub)
  app/onboarding/page.tsx              # first-run wizard (brand→competitors→integrations→prompts→"measuring…")
  lib/api.ts                           # typed fetch client (bearer from session), TanStack hooks
  lib/auth.ts                          # session/token handling, tenant from token (never client-set)
  components/ui/*                      # shadcn primitives
  components/charts/*                  # Recharts wrappers (SoV trend, sparkline)
  components/ConfidenceBadge.tsx       # CI + n_samples renderer (cross-cutting UX rule)
  pixel/gwgeo.ts                       # lead-capture SDK source → built to web/public/gwgeo.js
```

**Cross-cutting UX (ui-spec §4):** confidence always visible (`ConfidenceBadge` on every rate),
skeleton loaders, empty/first-run onboarding state until the first snapshot lands, multi-brand
switcher in the top bar, all views date-range + engine filterable.

---

## 5. Subsystem D — CRM/GA4 integrations (`src/gw_geo/attribution/integrations/`)

Pluggable connectors behind one interface; each **enriches `lead` rows** (CRM stage, deal value) and
**ingests conversions** (GA4). All HTTP `respx`-mocked in tests; secrets from SSM (never in repo).

```python
class Integration(Protocol):
    kind: str                                       # "hubspot" | "salesforce" | "ga4"
    def connect(self, session, *, tenant_id: str, config: dict) -> dict: ...   # -> {status}
    async def sync(self, session, *, tenant_id: str, brand_id: str) -> int: ...# -> rows synced
```

- **HubSpot / Salesforce (`crm.py`):** OAuth/token config → pull deal stage + amount, match to `lead`
  by email → update `lead.crm_stage`, `lead.value_usd`. Offline-conversion upload is a later add.
- **GA4 (`ga4.py`):** Data API pull of AI-referral channel sessions/conversions to corroborate the
  pixel; reconciliation only (pixel is system of record).
- Connection state persists in the new `integration` table (tenant-scoped, encrypted config ref).

---

## 6. Subsystem E — Lead-capture pixel/SDK

A tiny first-party JS SDK the client installs on their pages; the **origin of direct-referral data**
(TRD §6.1). `web/pixel/gwgeo.ts` (~2 KB, no deps) → built to `web/public/gwgeo.js`.

- Sets a first-party `visitor_id` cookie; on pageload beacons `{visitor_id, landing_url,
  document.referrer, utm, ua, ts}` to `POST /lead-capture/collect` (write-key from the snippet).
- Exposes `gwgeo.identify(email)` and `gwgeo.track('lead', {value})` for form/lead capture.
- `GET /lead-capture/snippet` (authed, Settings screen) returns the install `<script>` tag with the
  tenant/brand write-key. Beacon endpoint is **public but write-key-scoped** (maps key → tenant/brand
  server-side; a leaked key can only *write* that brand's sessions, never read).

---

## 7. Subsystem F — Auth, RBAC & tenancy (`src/gw_geo/api/auth.py`, `common/db.py`)

Self-contained backend JWT (decision §1).

- **New tables:** `app_user(id, email, password_hash, created_at)`,
  `membership(id, user_id, tenant_id, role)`. Roles: `owner | admin | editor | viewer` (ui-spec §5).
- **Login:** verify argon2 hash → issue access JWT (`sub=user_id, tenant_id, role`, ~15 min) +
  refresh token. `require_role("editor")` gates write routes; `viewer` is read-only.
- **Tenancy enforcement:** the `scoped_session()` dependency builds a `TenantScopedSession` (M0 T04)
  from the token's `tenant_id` — the same server-enforced filter used everywhere. A route **cannot**
  read another tenant even if a `brand_id` from another tenant is passed (404, not 403 leak).
- **Multi-brand:** a membership's tenant may own many brands; the top-bar switcher selects a brand,
  never a tenant.

---

## 8. Cross-cutting

- **Config (`Settings`):** add `jwt_secret`, `jwt_access_ttl_s: int = 900`, `jwt_refresh_ttl_s`,
  `cors_allow_origins: list[str]`, `hubspot_client_id/secret`, `salesforce_client_id/secret`,
  `ga4_property_id`, `ga4_credentials_ref`, `pixel_write_key_salt`. (Engine keys already exist.)
- **Data-model additions (Alembic migration, all tenant-scoped, `tenant_id` FK indexed):**
  - `session(id, tenant_id, brand_id, visitor_id, landing_url, referrer, utm jsonb, engine, user_agent, ts)`
  - `lead(id, tenant_id, brand_id, visitor_id, session_id, email, value_usd, crm_stage, self_reported_source, ts)`
  - `attribution_link(id, tenant_id, brand_id, lead_id, session_id, citation_id null, prompt_id null, engine, method, confidence, value_usd, ts)`
  - `holdout_cohort(id, tenant_id, brand_id, name, kind, prompt_ids jsonb, geo, is_holdout bool, started_at)`
  - `integration(id, tenant_id, kind, status, config_ref, connected_at)`
  - `app_user(...)`, `membership(...)` (§7).
  - `method` ∈ `direct | citation_linked | assisted | holdout_incremental`;
    `confidence` ∈ `high | medium | reported | modeled | low`.
- **Deps:** add `fastapi`, `uvicorn`, `mangum`, `PyJWT`, `argon2-cffi`, `python-multipart` (backend);
  `next`, `react`, `@tanstack/react-query`, `tailwindcss`, `recharts`, `vitest`, `@testing-library/react`,
  `@playwright/test` (web). No per-vendor SDKs for CRM/GA4 — `httpx` + `respx`, consistent with M0/M1.
- **Observability:** structured JSON logs already carry tenant/brand; API adds request-id + principal;
  attribution logs method + confidence per link.
- **Conventions (unchanged):** branch `m2/T<NN>-<slug>`, TDD, hermetic tests (`respx`/`moto`/SQLite;
  Vitest + Playwright component tests for `web/`), mypy-strict on `common/`,
  `Co-Authored-By: Claude Opus 4.8 (1M context)` trailer, per-task commit, orchestrator merges per
  wave. Everything local (no remote push).

---

## 9. Task DAG & waves (~21 tasks)

| Task | Depends on | Summary |
|---|---|---|
| M2-T01 config & secrets | M0 config | JWT, CORS, CRM/GA4, pixel keys in `Settings` |
| M2-T02 migrations | M0 db | `session, lead, attribution_link, holdout_cohort, integration, app_user, membership` |
| M2-T03 auth core + RBAC | T02 | argon2 + JWT issue/verify, `User`/`Membership`, roles |
| M2-T04 API skeleton + tenancy deps | T03 | FastAPI `create_app`, `scoped_session()`, `require_role`, error handlers |
| M2-T05 lead-capture pixel + ingestion | T02 | `pixel/gwgeo.ts`, `POST /lead-capture/collect`, `ingest_session/lead` |
| M2-T06 direct referral capture | T05 | `referral.py` engine-referrer classify → `attribution_link` |
| M2-T07 citation-to-page linkage | T05 | `linkage.py` landing_url ↔ citation.url |
| M2-T08 assisted modeling | T05 | `assisted.py` self-report + branded-lift (low-confidence) |
| M2-T09 holdout incrementality | T02 | `holdout.py` two-cohort lift + CI |
| M2-T10 pipeline aggregation | T06,T07,T08,T09 | `pipeline.py` method breakdown + confidence note |
| M2-T11 CRM integration | T05 | `integrations/crm.py` HubSpot/Salesforce enrich `lead` |
| M2-T12 GA4 integration | T05 | `integrations/ga4.py` referral reconciliation |
| M2-T13 brands + overview API | T04 (+M1 feed) | `GET/POST /brands`, `GET /brands/{id}/overview` |
| M2-T14 visibility + sources API | T04 (+M1 feed) | `GET /brands/{id}/visibility`, `/sources` |
| M2-T15 pipeline + alerts API | T04,T10 | `GET /brands/{id}/pipeline`, `/alerts` |
| M2-T16 settings/integrations/snippet API | T04,T11,T12,T05 | prompts CRUD, `POST /integrations/{kind}`, `GET /lead-capture/snippet` |
| M2-T17 web scaffold + auth + API client | T04 | Next.js app, layout/nav, `lib/api.ts`, login, TanStack |
| M2-T18 Overview + Visibility screens | T17,T13,T14 | 3.1/3.2 + `ConfidenceBadge` |
| M2-T19 Sources + Pipeline screens | T17,T14,T15 | 3.3/3.6 + method breakdown UI |
| M2-T20 Alerts + Settings + onboarding | T17,T15,T16 | 3.7/3.8 + first-run wizard |
| M2-T21 E2E + tenancy validation | T18,T19,T20 | Playwright happy path; cross-tenant isolation gate |

```
Wave 0 (foundation):        T01  T02
Wave 1 (auth/API/ingest):   T03  T04  T05  T09
Wave 2 (attribution/integr/read-API): T06 T07 T08  T10  T11 T12  T13 T14
Wave 3 (payoff API + dashboard): T15 T16  T17  T18 T19 T20  →  T21
```
Intra-wave notes (as in M1): in Wave 1, T04 needs T03 → T03 lands first. In Wave 2, T10 needs
T06/T07/T08/T09 → runs late in the wave; T13/T14 need only T04 + the M1 feed. In Wave 3, T15 needs
T10 (Wave 2); T16 needs T11/T12 (Wave 2); T17 needs T04; screens (T18–T20) need T17 + their
endpoints; T21 last.

---

## 10. Testing strategy

- **Hermetic CI (unchanged spine):** `respx` for CRM/GA4 HTTP; `moto` for AWS; **SQLite** for DB;
  no live calls in the default suite.
- **Backend (pytest):** attribution mechanisms unit-tested against seeded SQLite (known
  session/lead/citation rows → known links); holdout lift math property-tested (CI in [−1,1],
  monotonic in optimized-cohort gain). API tested with FastAPI `TestClient` + a fake principal;
  **every endpoint has a cross-tenant test** (tenant-B token cannot read tenant-A brand → 404).
- **Auth:** token issue/verify round-trip, expiry, `require_role` rejection matrix
  (viewer→write=403).
- **Frontend (Vitest + RTL):** components render contract shapes (mocked fetch); `ConfidenceBadge`
  shows CI + n; onboarding wizard state machine. **Playwright component/E2E:** login → onboard →
  overview → pipeline happy path against a mocked API; the tenancy isolation check is the release
  gate.
- **Pixel:** `gwgeo.ts` unit-tested (JSDOM) — sets cookie, builds beacon payload, calls collect.
- **Contract fidelity gate:** a schema test asserts each API response validates against the ui-spec
  §6 shape (the `web/` types are generated from / checked against the same schemas).

---

## 11. Confirmed decisions
1. **Auth:** self-contained **backend JWT** (argon2 + PyJWT), not Clerk/Auth0 — standalone.
2. **API framework:** **FastAPI** + `Mangum` (reuses the M0 Lambda target).
3. **Attribution honesty:** holdout incrementality is the only causal claim; all responses carry a
   `method_breakdown` + confidence note; UI never shows a bare attribution number.
4. **Pixel is system of record** for referrals; GA4 is reconciliation only.
5. **HTTP everywhere** for CRM/GA4 (`httpx`/`respx`), no per-vendor SDKs (consistent with M0/M1).
6. **Scope:** M2 = 6 screens + onboarding (Overview/Visibility/Sources/Pipeline/Alerts/Settings);
   Opportunities/Content/Seeding endpoints are M3/M4 and are not built.

## 12. Open items / risks
- Attribution is inherently fuzzy → **lead with holdouts + CIs, never overclaim causation** (PRD §13);
  the method breakdown is a hard UI requirement, not a nicety.
- Engine referrer hosts drift (new engines, `utm` conventions) → `AI_ENGINE_REFERRERS` is versioned
  config, unit-tested, easy to extend.
- Pixel write-key model must be leak-tolerant (write-only, per-brand) — verified in T05 tests.
- CRM/GA4 provider API shapes must be verified against current docs when each connector is built
  (as with M1 engine adapters).
- SSO (SAML/OIDC) and offline-conversion upload are Settings stubs in M2, built later.
- `web/` is the first TypeScript surface in the repo → CI gains a `web` job (lint/typecheck/vitest/
  playwright) alongside the Python job.
