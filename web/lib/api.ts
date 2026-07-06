import { getToken as defaultGetToken } from "./auth";
import type {
  Alert,
  Brand,
  BrandCreated,
  BrandSuggestion,
  ContentApproveResponse,
  ContentGenerateResponse,
  ContentPublishResponse,
  IntegrationKind,
  IntegrationResult,
  KbFactIn,
  KbFactsIngested,
  MeasureAccepted,
  MeasureRequest,
  Opportunity,
  OpportunityActResponse,
  OpportunityRefreshAccepted,
  Overview,
  Pipeline,
  Prompt,
  SnippetResponse,
  Source,
  VisibilityResponse,
} from "./types";

/** Backend base URL. Empty string = same-origin (see README / NEXT_PUBLIC_API_URL). */
export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type QueryParams = Record<string, string | undefined>;

function queryString(params: QueryParams): string {
  const usp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") usp.set(key, value);
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

/** Typed client bound to the ui-spec §6 contract. */
export type ApiClient = {
  // Reads
  brands(): Promise<Brand[]>;
  overview(brandId: string, range: string): Promise<Overview>;
  visibility(
    brandId: string,
    q: { range?: string; geo?: string; persona?: string },
  ): Promise<VisibilityResponse>;
  sources(brandId: string, range: string): Promise<Source[]>;
  pipeline(brandId: string, range: string): Promise<Pipeline>;
  alerts(brandId: string): Promise<Alert[]>;
  prompts(brandId: string): Promise<Prompt[]>;
  // Opportunities (3.4)
  opportunities(brandId: string): Promise<Opportunity[]>;
  /** `POST /brands/{id}/opportunities/refresh` — 202; then re-read `opportunities`. */
  refreshOpportunities(brandId: string): Promise<OpportunityRefreshAccepted>;
  /** `POST /opportunities/{id}/act` — spawn a pre-scoped content draft; returns its `content_id`. */
  actOnOpportunity(opportunityId: string): Promise<OpportunityActResponse>;
  // Content engine (3.5)
  /** `POST /brands/{id}/kb/facts` — populate the grounding corpus (role ≥ editor). */
  ingestKbFacts(brandId: string, facts: KbFactIn[]): Promise<KbFactsIngested>;
  generateContent(input: {
    brand_id: string;
    prompt_text: string;
    target_engine?: string;
  }): Promise<ContentGenerateResponse>;
  /** `POST /content/{id}/approve` — human gate (role ≥ editor); 409 if guardrails/role fail. */
  approveContent(contentId: string): Promise<ContentApproveResponse>;
  /** `POST /content/{id}/publish` — publish an approved draft (role ≥ editor); 409 if not approved. */
  publishContent(contentId: string, connector?: string): Promise<ContentPublishResponse>;
  // Writes (Settings)
  createBrand(input: {
    name: string;
    domain: string;
    competitors?: string[];
  }): Promise<BrandCreated>;
  /**
   * `POST /brands/suggest` — domain-first onboarding auto-fill (role ≥ editor). Given a domain,
   * returns a suggested brand `name` (read off the site) + likely `competitors`, both editable.
   * Purely advisory: performs no DB write.
   */
  suggestBrand(domain: string): Promise<BrandSuggestion>;
  /**
   * `POST /brands/{id}/measure` — kick off a measurement run (role ≥ editor;
   * brand-ownership checked server-side). Returns **202** with the scheduled run
   * (`engines` + `n_samples`); the data itself lands async minutes later. An
   * empty `body` (the default `{}`) uses server defaults for engines/geos/n.
   */
  measureBrand(brandId: string, body?: MeasureRequest): Promise<MeasureAccepted>;
  /**
   * Persist prompts for a brand. The backend exposes only a *singular* create
   * (`POST /brands/{id}/prompts`, one `PromptCreate` -> `{id}`), so this maps the
   * array to N sequential creates (array order preserved) and returns the created
   * rows carrying their real backend ids. Callers pass the prompts to add.
   */
  savePrompts(brandId: string, prompts: Prompt[]): Promise<Prompt[]>;
  connectIntegration(
    kind: IntegrationKind,
    config: Record<string, unknown>,
  ): Promise<IntegrationResult>;
  /** `GET /lead-capture/snippet?brand_id=` — the brand_id query param is required by the backend. */
  leadCaptureSnippet(brandId: string): Promise<SnippetResponse>;
};

/**
 * Build an API client. `getToken` is injected so it works in tests and in the
 * app (defaults to the session store). Every request sends
 * `Authorization: Bearer <token>`; a 401 clears nothing but redirects to /login.
 */
export function apiClient(
  getToken: () => string | null = defaultGetToken,
): ApiClient {
  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const token = getToken();
    // Plain-object headers (never a Headers instance) so callers/tests can read
    // headers.Authorization directly.
    const headers: Record<string, string> = {
      ...(init?.headers as Record<string, string> | undefined),
    };
    if (token) headers.Authorization = `Bearer ${token}`;
    if (init?.body !== undefined && !("Content-Type" in headers)) {
      headers["Content-Type"] = "application/json";
    }

    const res = await fetch(`${API_BASE}${path}`, { ...init, headers });

    if (res.status === 401) {
      if (typeof window !== "undefined") {
        window.location.href = "/login";
      }
      throw new ApiError(401, "Unauthorized");
    }
    if (!res.ok) {
      throw new ApiError(res.status, `Request to ${path} failed (${res.status})`);
    }
    return (await res.json()) as T;
  }

  const brandPath = (id: string) => `/brands/${encodeURIComponent(id)}`;

  return {
    brands: () => request<Brand[]>("/brands"),
    overview: (brandId, range) =>
      request<Overview>(`${brandPath(brandId)}/overview${queryString({ range })}`),
    visibility: (brandId, q) =>
      request<VisibilityResponse>(`${brandPath(brandId)}/visibility${queryString(q)}`),
    sources: (brandId, range) =>
      request<Source[]>(`${brandPath(brandId)}/sources${queryString({ range })}`),
    pipeline: (brandId, range) =>
      request<Pipeline>(`${brandPath(brandId)}/pipeline${queryString({ range })}`),
    alerts: (brandId) => request<Alert[]>(`${brandPath(brandId)}/alerts`),
    prompts: (brandId) => request<Prompt[]>(`${brandPath(brandId)}/prompts`),
    opportunities: (brandId) =>
      request<Opportunity[]>(`${brandPath(brandId)}/opportunities`),
    refreshOpportunities: (brandId) =>
      request<OpportunityRefreshAccepted>(`${brandPath(brandId)}/opportunities/refresh`, {
        method: "POST",
      }),
    actOnOpportunity: (opportunityId) =>
      request<OpportunityActResponse>(
        `/opportunities/${encodeURIComponent(opportunityId)}/act`,
        { method: "POST" },
      ),
    ingestKbFacts: (brandId, facts) =>
      request<KbFactsIngested>(`${brandPath(brandId)}/kb/facts`, {
        method: "POST",
        body: JSON.stringify(facts),
      }),
    generateContent: (input) =>
      request<ContentGenerateResponse>("/content/generate", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    approveContent: (contentId) =>
      request<ContentApproveResponse>(
        `/content/${encodeURIComponent(contentId)}/approve`,
        { method: "POST" },
      ),
    publishContent: (contentId, connector = "hosted") =>
      request<ContentPublishResponse>(
        `/content/${encodeURIComponent(contentId)}/publish`,
        { method: "POST", body: JSON.stringify({ connector }) },
      ),
    createBrand: (input) =>
      request<BrandCreated>("/brands", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    suggestBrand: (domain) =>
      request<BrandSuggestion>("/brands/suggest", {
        method: "POST",
        body: JSON.stringify({ domain }),
      }),
    measureBrand: (brandId, body = {}) =>
      request<MeasureAccepted>(`${brandPath(brandId)}/measure`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    savePrompts: async (brandId, prompts) => {
      // Backend has only a singular create; issue one POST per prompt (sequential to keep the
      // array's order = priority order) and return the created rows with their real ids.
      const created: Prompt[] = [];
      for (const p of prompts) {
        const { id } = await request<BrandCreated>(`${brandPath(brandId)}/prompts`, {
          method: "POST",
          body: JSON.stringify({
            text: p.text,
            intent_cluster: p.intent_cluster,
            geo: p.geo,
            persona: p.persona,
          }),
        });
        created.push({ ...p, id });
      }
      return created;
    },
    connectIntegration: (kind, config) =>
      // Backend expects the config wrapped in a `{ config }` envelope (IntegrationConnect);
      // POSTing the raw dict 422s. TODO(M3): surface a real credential-ref input form in
      // IntegrationsPanel/OnboardingWizard so `config` carries an actual secret-store pointer
      // (currently always `{}` from the callers) — for now this only fixes the wire shape.
      request<IntegrationResult>(`/integrations/${encodeURIComponent(kind)}`, {
        method: "POST",
        body: JSON.stringify({ config }),
      }),
    leadCaptureSnippet: (brandId) =>
      request<SnippetResponse>(
        `/lead-capture/snippet${queryString({ brand_id: brandId })}`,
      ),
  };
}
