/**
 * Shared test harness for the dashboard screens.
 *
 * - `renderWithClient(ui, options?)` wraps `ui` in a fresh TanStack
 *   `QueryClientProvider` (retries off, no cache bleed between tests) plus the
 *   app's `FiltersProvider`, so components that call `useQuery` / `useFilters`
 *   render in isolation.
 * - `mockApi(overrides)` replaces `apiClient(...)` so every method resolves to
 *   canned data. Unspecified endpoints fall back to sane, non-empty defaults
 *   (e.g. one brand) so screens can auto-select a brand without extra setup.
 *
 * Imported everywhere as `@/test/utils`.
 */
import { render, type RenderResult } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { ReactElement } from "react";

import * as apiModule from "@/lib/api";
import type { ApiClient } from "@/lib/api";
import {
  FiltersProvider,
  DEFAULT_RANGE,
  ALL_ENGINES,
  type FiltersState,
} from "@/lib/filters";
import type {
  Alert,
  Brand,
  ContentGenerateResponse,
  LlmModelConfig,
  MeasureAccepted,
  Opportunity,
  Overview,
  Pipeline,
  Prompt,
  SnippetResponse,
  Source,
  VisibilityResponse,
} from "@/lib/types";

/**
 * Per-endpoint override map. Keys cover **every** endpoint the screens use so
 * downstream tasks can drive any screen: `mockApi({ pipeline: {…} })`,
 * `mockApi({ sources: [...] })`, `mockApi({ alerts: [...] })`, etc.
 */
export type MockApiOverrides = {
  brands?: Brand[];
  overview?: Overview;
  visibility?: VisibilityResponse;
  sources?: Source[];
  pipeline?: Pipeline;
  alerts?: Alert[];
  prompts?: Prompt[];
  snippet?: SnippetResponse;
  opportunities?: Opportunity[];
  /** Canned `POST /content/generate` result (Content screen). */
  content?: ContentGenerateResponse;
  /** Canned `POST /brands/{id}/measure` 202 acknowledgement (Run-measurement button). */
  measure?: MeasureAccepted;
  /** Canned `GET /settings/llm-model` rows (Settings LLM-model panel). */
  llmModel?: LlmModelConfig[];
};

const DEFAULT_BRANDS: Brand[] = [
  { id: "b1", name: "Acme", domain: "acme.com", competitors: ["Beta"] },
];

const DEFAULTS: Required<MockApiOverrides> = {
  brands: DEFAULT_BRANDS,
  overview: { sov: 0, mention_rate: 0, pipeline: 0, leads: 0, trend: [] },
  visibility: { engines: [], prompts: [] },
  sources: [],
  pipeline: {
    influenced: 0,
    attributed: 0,
    leads: 0,
    lift: 0,
    top_answers: [],
    method_breakdown: {
      direct: 0,
      citation_linked: 0,
      assisted: 0,
      holdout_incremental: 0,
    },
    confidence_note: "",
  },
  alerts: [],
  prompts: [],
  snippet: { snippet: "" },
  opportunities: [],
  content: {
    content_id: "c1",
    draft: {
      id: "c1",
      tenant_id: "t1",
      brand_id: "b1",
      prompt_id: null,
      target_engine: null,
      intent_cluster: null,
      title: "Draft title",
      body_markdown: "Draft body.",
      schema_jsonld: {},
      grounded_fact_ids: [],
      status: "draft",
    },
    guardrails: { claims_ok: true, originality_ok: true },
  },
  measure: {
    status: "accepted",
    brand_id: "b1",
    engines: ["perplexity", "openai"],
    n_samples: 8,
  },
  llmModel: [
    { gateway: "local_claude", chat_model: "sonnet" },
    { gateway: "portkey", chat_model: "claude-haiku-4-5-20251001" },
    { gateway: "direct", chat_model: "claude-opus-4-8" },
  ],
};

/**
 * Install a fake `apiClient`. Every method resolves to the override (or a
 * default). Returns the fake client so a test can assert on / tweak it.
 */
export function mockApi(overrides: MockApiOverrides = {}): ApiClient {
  const data = { ...DEFAULTS, ...overrides };

  const client: ApiClient = {
    brands: () => Promise.resolve(data.brands),
    overview: () => Promise.resolve(data.overview),
    visibility: () => Promise.resolve(data.visibility),
    sources: () => Promise.resolve(data.sources),
    pipeline: () => Promise.resolve(data.pipeline),
    alerts: () => Promise.resolve(data.alerts),
    prompts: () => Promise.resolve(data.prompts),
    opportunities: () => Promise.resolve(data.opportunities),
    refreshOpportunities: (brandId) =>
      Promise.resolve({ status: "accepted", brand_id: brandId }),
    actOnOpportunity: () => Promise.resolve({ content_id: "spawned-content" }),
    ingestKbFacts: (_brandId, facts) => Promise.resolve({ added: facts.length }),
    generateContent: () => Promise.resolve(data.content),
    approveContent: () => Promise.resolve({ status: "approved" }),
    publishContent: () =>
      Promise.resolve({ status: "published", published_url: "https://hosted.gwgeo.io/p/c1" }),
    createBrand: () => Promise.resolve({ id: "new-brand" }),
    suggestBrand: (domain) =>
      Promise.resolve({ name: "Acme", domain, competitors: ["Beta"] }),
    measureBrand: (brandId) => Promise.resolve({ ...data.measure, brand_id: brandId }),
    savePrompts: (_brandId, prompts) => Promise.resolve(prompts),
    connectIntegration: () => Promise.resolve({ status: "connected" }),
    leadCaptureSnippet: () => Promise.resolve(data.snippet),
    llmModelConfig: () => Promise.resolve(data.llmModel),
    setLlmModelConfig: (input) => Promise.resolve(input),
  };

  vi.spyOn(apiModule, "apiClient").mockReturnValue(client);
  return client;
}

export type RenderWithClientOptions = Partial<FiltersState>;

/** Render `ui` inside a fresh QueryClient + the app FiltersProvider. */
export function renderWithClient(
  ui: ReactElement,
  options: RenderWithClientOptions = {},
): RenderResult {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <FiltersProvider
        initialBrandId={options.brandId ?? null}
        initialRange={options.range ?? DEFAULT_RANGE}
        initialEngine={options.engine ?? ALL_ENGINES}
      >
        {ui}
      </FiltersProvider>
    </QueryClientProvider>,
  );
}
