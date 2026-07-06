// API contract types — mirror the backend REST shapes verbatim (ui-spec §6 /
// TRD §11). Field names stay snake_case to match the JSON on the wire; there is
// no client-side remapping so screens can consume responses directly.

export type Brand = {
  id: string;
  name: string;
  domain: string;
  competitors: string[];
};

export type BrandCreated = { id: string };

/**
 * `POST /brands/suggest` → domain-first onboarding auto-fill (mirrors the backend
 * `BrandSuggestion`): a brand `name` read off the site, the echoed `domain`, and up to ~6
 * suggested `competitors`. Every field is a suggestion the user edits in the wizard before
 * `POST /brands` — nothing is persisted by this call.
 */
export type BrandSuggestion = {
  name: string;
  domain: string;
  competitors: string[];
};

// ── Measurement trigger (W2) ──────────────────────────────────────────────────
/**
 * `POST /brands/{id}/measure` body — all optional run overrides (mirrors the
 * backend `MeasureTriggerRequest`). An empty body `{}` means "use server
 * defaults": every API-keyed engine, the default geos, and the default sample
 * size. `tenant_id` is NEVER in the body — it is derived from the bearer token.
 */
export type MeasureRequest = {
  engines?: string[];
  geos?: string[];
  n_samples?: number;
  date?: string;
};

/**
 * `POST /brands/{id}/measure` → **202** acknowledgement (mirrors `MeasureAccepted`).
 * The run itself is async on the backend, so this only echoes what was scheduled
 * (the resolved `engines` + `n_samples`), not any result.
 */
export type MeasureAccepted = {
  status: string;
  brand_id: string;
  engines: string[];
  n_samples: number;
};

// ── Overview (3.1) ──────────────────────────────────────────────────────────
export type OverviewTrendPoint = {
  date: string;
  you: number;
  competitor: number;
};

export type Overview = {
  sov: number;
  mention_rate: number;
  pipeline: number;
  leads: number;
  trend: OverviewTrendPoint[];
};

// ── Visibility (3.2) ─────────────────────────────────────────────────────────
export type EngineTrendPoint = { date: string; mention_rate: number };

export type EngineRow = {
  engine: string;
  mention_rate: number;
  /** 95% confidence interval [low, high] as 0..1 rates. */
  ci: [number, number];
  cited: number;
  avg_position: number | null;
  /**
   * Raw sentiment score in −1..1 (the backend never buckets it into a label; the ui-spec §6
   * schema pins it to `number`). `EngineTable` maps this score to an emoji by threshold.
   */
  sentiment: number;
  n_samples: number;
  trend: EngineTrendPoint[];
};

/**
 * Prompt-management shape — the `GET/POST /brands/{id}/prompts` set that Settings
 * edits (ui-spec §6, §3.8). Distinct from the prompt-level *metrics* returned
 * inside the visibility response (see `PromptMetric`).
 */
export type Prompt = {
  id: string;
  text: string;
  // Backend allows null for both (ui-spec §6): a prompt need not be clustered or persona-tagged.
  intent_cluster: string | null;
  geo: string;
  persona: string | null;
};

/**
 * Per-prompt metrics returned inside the visibility response (T14 / ui-spec
 * §3.2): the prompt-level table + "view sampled answers" drawer read these.
 */
export type PromptMetric = {
  prompt_id: string;
  text: string;
  mention_rate: number;
  avg_position: number | null;
  /** Number of sampled answers behind this prompt's metrics. */
  n_samples: number;
};

export type VisibilityResponse = {
  engines: EngineRow[];
  prompts: PromptMetric[];
};

// ── Sources (3.3) ────────────────────────────────────────────────────────────
export type Source = {
  domain: string;
  source_type: string;
  /** Share of answers that cite you from this source (0..1). */
  you_pct: number;
  /** competitor name -> cite share (0..1). */
  competitor_pcts: Record<string, number>;
};

// ── Pipeline (3.6) ───────────────────────────────────────────────────────────
export type AttributionMethod =
  | "direct"
  | "citation_linked"
  | "assisted"
  | "holdout_incremental";

export type TopAnswer = { prompt: string; leads: number; value: number };

export type Pipeline = {
  influenced: number;
  attributed: number;
  leads: number;
  lift: number;
  top_answers: TopAnswer[];
  method_breakdown: Record<AttributionMethod, number>;
  confidence_note: string;
};

// ── Alerts (3.7) ─────────────────────────────────────────────────────────────
export type AlertSeverity = "red" | "yellow" | "green" | (string & {});

export type Alert = {
  severity: AlertSeverity;
  message: string;
  ts: string;
};

// ── Settings write endpoints (3.8) ──────────────────────────────────────────
export type IntegrationKind = "crm" | "ga4" | "cms" | "lead_capture" | (string & {});
export type IntegrationResult = { status: string };
export type SnippetResponse = { snippet: string };

/**
 * `GET/PUT /settings/llm-model` (M5) — the operator-selected content-chat model per env-driven
 * gateway (`local_claude`/`portkey`/`direct`). Only the *model* is DB-stored/selectable; the
 * *gateway* stays env-driven (`GEO_LLM_GATEWAY`). System-level (admin-gated), not tenant-scoped.
 */
export type LlmModelConfig = { gateway: string; chat_model: string };

// ── Opportunities (3.4) ───────────────────────────────────────────────────────
// The ranked-gap row shape (ui-spec §6 / OpportunityOut, verbatim). The underlying
// tenant_id/brand_id/source_gap/status stay server-side and are not exposed here.
export type Opportunity = {
  id: string;
  title: string;
  rationale: string;
  /** Estimated impact (engine weight × gap size, 0..1) — rendered as a %. */
  est_impact: number;
  /** The engine the gap is on, or null for a cross-engine / absence gap. */
  engine: string | null;
};

/** `POST /opportunities/{id}/act` → the content draft the "Fix this" action spawned. */
export type OpportunityActResponse = { content_id: string };

/** `POST /brands/{id}/opportunities/refresh` → 202 acknowledgement (async re-rank). */
export type OpportunityRefreshAccepted = { status: string; brand_id: string };

// ── Content engine (3.5) ──────────────────────────────────────────────────────
export type ContentStatus =
  | "draft"
  | "pending_review"
  | "approved"
  | "published"
  | "rejected"
  | (string & {});

/**
 * The server-authoritative draft returned by `POST /content/generate` (mirrors
 * `gw_geo.common.models.ContentDraft`). The screen renders `title` + `body_markdown`
 * and carries `id` (= content_id) into the approve/publish gate.
 */
export type ContentDraft = {
  id: string;
  tenant_id: string;
  brand_id: string;
  prompt_id: string | null;
  target_engine: string | null;
  intent_cluster: string | null;
  title: string;
  body_markdown: string;
  schema_jsonld: Record<string, unknown>;
  grounded_fact_ids: string[];
  status: ContentStatus;
};

/** One fact in the `POST /brands/{id}/kb/facts` ingest body. */
export type KbFactIn = { text: string; category?: string; source?: string };
/** `POST /brands/{id}/kb/facts` → how many facts were embedded + upserted. */
export type KbFactsIngested = { added: number };

/** The two guardrail badges the Content screen renders (ui-spec §6). */
export type GuardrailBadges = { claims_ok: boolean; originality_ok: boolean };

export type ContentGenerateResponse = {
  content_id: string;
  draft: ContentDraft;
  guardrails: GuardrailBadges;
};

export type ContentApproveResponse = { status: string };
export type ContentPublishResponse = { status: string; published_url: string };
