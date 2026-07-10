export type AiIndexPage = {
  path: string;
  type: "blog" | "category" | "service" | "topics" | "page";
  url: string;
  title: string;
  description: string;
  last_modified_at: number | null;
  markdown: string;
};

export type AiIndexBusiness = {
  name: string;
  legal_name: string;
  address: string;
  phones: string[];
  emails: string[];
  hours: Record<string, string>;
  service_areas: string[];
  certifications: string[];
  about: string;
};

export type AiIndexProduct = {
  name: string;
  url: string;
  attributes: Record<string, string | string[]>;
};

export type AiIndexService = {
  name: string;
  description: string;
};

export type AiIndexReview = {
  reviewer: string;
  content: string;
  source: string;
};

export type AiIndexSite = {
  name: string;
  domain: string;
  base_url: string;
  base_path: string;
  description: string;
};

export type AiIndex = {
  version: number;
  generated_at: string;
  site: AiIndexSite;
  business: AiIndexBusiness;
  pages: AiIndexPage[];
  products: AiIndexProduct[];
  services: AiIndexService[];
  reviews: AiIndexReview[];
};

export type JsonRpcRequest = {
  jsonrpc: "2.0";
  id?: string | number | null;
  method: string;
  params?: Record<string, unknown>;
};

export type JsonRpcResponse = {
  jsonrpc: "2.0";
  id: string | number | null;
  result?: unknown;
  error?: { code: number; message: string };
};

export type ToolContent = {
  content: { type: "text"; text: string }[];
  isError?: boolean;
};

export type RpcContext = {
  index: AiIndex | null;
  rootDomain: string;
  hostname: string;
};
