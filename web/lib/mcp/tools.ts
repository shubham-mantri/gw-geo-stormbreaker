import type { AiIndex, RpcContext, ToolContent } from "./types";
import { searchIndex } from "./search";

const toolText = (text: string): ToolContent => ({
  content: [{ type: "text", text }],
});
const toolError = (text: string): ToolContent => ({
  content: [{ type: "text", text }],
  isError: true,
});

const INDEX_MISSING =
  "This site's AI index is not available yet. Try the website directly.";

const MAX_MESSAGE_CHARS = 2000;
const LEAD_API = {
  prod: "https://api.gushwork.ai/seo-v2/lead",
  dev: "https://api-dev.gushwork.ai/seo-v2/lead",
};

export const TOOL_DEFINITIONS = [
  {
    name: "search_site",
    description:
      "Search this website's pages (services, product categories, blog articles). Returns ranked results with title, URL, and a content snippet.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Search query" },
        limit: { type: "number", description: "Max results (default 5, max 20)" },
      },
      required: ["query"],
    },
  },
  {
    name: "get_page",
    description:
      "Get the full content of a page as markdown, by its path (e.g. 'blog/o-ring-alternatives' or '' for the homepage). Use search_site or list_pages to discover paths.",
    inputSchema: {
      type: "object",
      properties: {
        path: {
          type: "string",
          description: "Page path from search_site/list_pages results",
        },
      },
      required: ["path"],
    },
  },
  {
    name: "list_pages",
    description:
      "List all pages on this site with title, URL and description. Optionally filter by type.",
    inputSchema: {
      type: "object",
      properties: {
        type: {
          type: "string",
          enum: ["blog", "category", "service", "topics", "page"],
          description: "Optional page type filter",
        },
      },
    },
  },
  {
    name: "get_business_info",
    description:
      "Get structured business information: legal/trade name, address, phone numbers, email, working hours, service areas, certifications, and company background.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "list_services",
    description: "List the services this business offers, with descriptions.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "list_products",
    description:
      "List the products this business offers, with attributes. Optionally filter by a query string.",
    inputSchema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description:
            "Optional filter, matched against product names and attributes",
        },
      },
    },
  },
  {
    name: "get_reviews",
    description: "Get customer reviews/testimonials for this business.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "submit_inquiry",
    description:
      "Submit a sales/contact inquiry to this business on behalf of the user. Only use when the user explicitly asks to contact the business, and confirm the details with them first.",
    inputSchema: {
      type: "object",
      properties: {
        name: { type: "string", description: "The user's name" },
        email: { type: "string", description: "The user's email address" },
        phone: { type: "string", description: "The user's phone number (optional)" },
        message: {
          type: "string",
          description: "What the user needs (max 2000 chars)",
        },
        page_path: {
          type: "string",
          description: "Path of the page/product the inquiry is about (optional)",
        },
      },
      required: ["name", "email", "message"],
    },
  },
];

type ToolArgs = Record<string, unknown>;

function requireIndex(
  fn: (args: ToolArgs, ctx: RpcContext) => ToolContent | Promise<ToolContent>,
) {
  return (args: ToolArgs, ctx: RpcContext): ToolContent | Promise<ToolContent> =>
    ctx.index ? fn(args, ctx) : toolError(INDEX_MISSING);
}

export const TOOL_HANDLERS: Record<
  string,
  (args: ToolArgs, ctx: RpcContext) => ToolContent | Promise<ToolContent>
> = {
  search_site: requireIndex(({ query, limit }, { index }) => {
    const capped = Math.min(Math.max(Number(limit) || 5, 1), 20);
    const results = searchIndex(index!, String(query || ""), capped);
    if (!results.length)
      return toolText(
        `No pages matched "${query}". Try list_pages to browse all pages.`,
      );
    return toolText(
      results
        .map(
          ({ page, snippet }, i) =>
            `${i + 1}. ${page.title}\n   URL: ${page.url}\n   Path: ${page.path}\n   ${snippet}`,
        )
        .join("\n\n"),
    );
  }),

  get_page: requireIndex(({ path }, { index }) => {
    const normalized = String(path ?? "").replace(/^\/+|\/+$/g, "");
    const page = index!.pages.find((p) => p.path === normalized);
    if (!page)
      return toolError(
        `No page found at path "${path}". Use list_pages or search_site to find valid paths.`,
      );
    return toolText(`# ${page.title}\nURL: ${page.url}\n\n${page.markdown}`);
  }),

  list_pages: requireIndex(({ type }, { index }) => {
    const pages = type
      ? index!.pages.filter((p) => p.type === type)
      : index!.pages;
    if (!pages.length)
      return toolText(`No pages${type ? ` of type "${type}"` : ""}.`);
    return toolText(
      pages
        .map(
          (p) =>
            `- [${p.type}] ${p.title} — ${p.url} (path: ${p.path || "/"})${p.description ? `\n  ${p.description}` : ""}`,
        )
        .join("\n"),
    );
  }),

  get_business_info: requireIndex((_args, { index }) => {
    const b = index!.business;
    return toolText(
      JSON.stringify(
        {
          name: b.name,
          legal_name: b.legal_name,
          website: index!.site.base_url,
          address: b.address,
          phones: b.phones,
          emails: b.emails,
          working_hours: b.hours,
          service_areas: b.service_areas,
          certifications: b.certifications,
          about: b.about,
        },
        null,
        2,
      ),
    );
  }),

  list_services: requireIndex((_args, { index }) => {
    if (!index!.services.length)
      return toolText(
        'No structured service list available; try list_pages with type "service".',
      );
    return toolText(
      index!.services.map((s) => `- ${s.name}: ${s.description}`).join("\n"),
    );
  }),

  list_products: requireIndex(({ query }, { index }) => {
    let products = index!.products;
    if (query) {
      const q = String(query).toLowerCase();
      products = products.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          JSON.stringify(p.attributes).toLowerCase().includes(q),
      );
    }
    if (!products.length)
      return toolText(`No products${query ? ` matching "${query}"` : ""}.`);
    return toolText(
      products
        .map((p) => {
          const attrs = Object.entries(p.attributes || {})
            .map(([k, v]) => `${k}: ${[].concat(v as never).join(", ")}`)
            .join("; ");
          return `- ${p.name}${p.url ? ` — ${p.url}` : ""}${attrs ? `\n  ${attrs}` : ""}`;
        })
        .join("\n"),
    );
  }),

  get_reviews: requireIndex((_args, { index }) => {
    if (!index!.reviews.length) return toolText("No reviews available.");
    return toolText(
      index!.reviews.map((r) => `"${r.content}" — ${r.reviewer}`).join("\n\n"),
    );
  }),

  submit_inquiry: async (
    { name, email, phone, message, page_path },
    { index, rootDomain },
  ) => {
    if (!name || typeof name !== "string") return toolError("A name is required.");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(email || "")))
      return toolError("A valid email address is required.");
    if (!message || typeof message !== "string")
      return toolError("A message is required.");
    if ((message as string).length > MAX_MESSAGE_CHARS)
      return toolError(`Message too long (max ${MAX_MESSAGE_CHARS} characters).`);

    const page = index?.pages?.find(
      (p) => p.path === String(page_path ?? "").replace(/^\/+|\/+$/g, ""),
    );
    const apiPath = LEAD_API.dev;
    const payload = {
      request_origin: rootDomain,
      leads_info: { name, email, ...(phone ? { phone } : {}), message },
      form_details: {
        requestType: "MCP",
        formTitle: "MCP submit_inquiry",
        formId: "mcp-agent",
        productName: page?.title ?? "",
      },
    };

    try {
      const res = await fetch(apiPath, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        return toolError(
          "The inquiry could not be submitted right now. Please use the contact form on the website instead.",
        );
      }
      return toolText(
        `Inquiry submitted to ${index?.site?.name || rootDomain}. They typically respond via the email provided (${email}).`,
      );
    } catch {
      return toolError(
        "The inquiry could not be submitted right now. Please use the contact form on the website instead.",
      );
    }
  },
};
