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
