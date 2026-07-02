import { getToken as defaultGetToken } from "./auth";
import type {
  Alert,
  Brand,
  BrandCreated,
  IntegrationKind,
  IntegrationResult,
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
  // Writes (Settings)
  createBrand(input: {
    name: string;
    domain: string;
    competitors?: string[];
  }): Promise<BrandCreated>;
  savePrompts(brandId: string, prompts: Prompt[]): Promise<Prompt[]>;
  connectIntegration(
    kind: IntegrationKind,
    config: Record<string, unknown>,
  ): Promise<IntegrationResult>;
  leadCaptureSnippet(): Promise<SnippetResponse>;
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
    createBrand: (input) =>
      request<BrandCreated>("/brands", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    savePrompts: (brandId, prompts) =>
      request<Prompt[]>(`${brandPath(brandId)}/prompts`, {
        method: "POST",
        body: JSON.stringify(prompts),
      }),
    connectIntegration: (kind, config) =>
      request<IntegrationResult>(`/integrations/${encodeURIComponent(kind)}`, {
        method: "POST",
        body: JSON.stringify(config),
      }),
    leadCaptureSnippet: () => request<SnippetResponse>("/lead-capture/snippet"),
  };
}
