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
  sentiment: string;
  n_samples: number;
  trend: EngineTrendPoint[];
};

export type Prompt = {
  id: string;
  text: string;
  intent_cluster: string;
  geo: string;
  persona: string;
};

export type VisibilityResponse = {
  engines: EngineRow[];
  prompts: Prompt[];
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
