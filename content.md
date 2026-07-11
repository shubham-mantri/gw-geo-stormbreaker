# MCP Discovery Document (/.well-known/mcp)

## What it does
When an AI agent (Claude, ChatGPT, a custom MCP client) visits any website, the first thing it checks is:

GET https://example.com/.well-known/mcp
Our response looks like this:

{
  "servers": [
    {
      "name": "Mixing Systems, Inc. MCP",
      "description": "Open MCP server for mixing.com: site search, page content, business info, products, services, reviews, and inquiries.",
      "url": "http://localhost:3099/api/mcp",
      "transport": "streamable-http",
      "authentication": { "type": "none" }
    }
  ]
}

That's it — a tiny JSON document that says: "Yes, I have an MCP server. Here's where it lives and how to talk to it."

## Why it exists
Without this, an AI agent visiting mixing.com has no way to know the site is AI-queryable. It would just see HTML like any other website.

Think of it as a signpost:

robots.txt → tells search crawlers what they can/can't index
sitemap.xml → tells search crawlers where all pages are
.well-known/mcp → tells AI agents "I speak MCP, connect here"
It's the difference between a website that AI can only scrape vs. one that AI can have a structured conversation with.

How it works in the flow

AI Agent arrives at mixing.com
        │
        ▼
GET /.well-known/mcp
        │
        ▼
Discovers: "There's an MCP server at /api/mcp, no auth needed"
        │
        ▼
POST /api/mcp → { method: "initialize" }   ← This is the Handshake
        │
        ▼
POST /api/mcp → { method: "tools/call", params: { name: "search_site", ... } }

Discovery is step 0 — it happens before the handshake, before any tool call. The agent uses it to decide whether to even attempt a connection.

## Key fields explained
name: Human-readable server name (shows in client UI)
description: What capabilities this server offers
url: The actual endpoint to POST JSON-RPC to
transport: Protocol — streamable-http is the standard for web-based MCP
authentication.type: "none" = open/public. Could be "bearer" for private servers

## Questions people typically ask
"Does every website need this?"
No — only if you want AI agents to interact with your site programmatically. Most sites don't have it. Having it is a competitive advantage for AI visibility.

"Is it different per project?"
In our system, yes — after you sync a project, the name and description reflect that project's business. In production (when deployed per-client), each client's domain would have their own discovery doc pointing to their own MCP server.

"What if someone doesn't have this file?"
The AI agent simply moves on. It treats the site as a regular website — scrape-only, no structured tools. The site becomes invisible to tool-using AI agents.

"Can there be multiple servers?"
Yes, the servers array can list multiple. For example: one for product search, another for support tickets. We have one.

"Is authentication: none a security problem?"
No — it's intentionally public. The whole point is that any AI agent can discover and query the site. Same as making your website publicly accessible. The submit_inquiry tool has its own validation (requires name + email + message) to prevent abuse.

## Where it sits in the complete flow

[Discovery]  →  [Handshake]  →  [Tool Calls]  →  [Lead Capture]
     ▲
  YOU ARE HERE

"Does this site speak AI?"     "What can you do?"     "Do it."     "Contact them."

- Discovery is the advertising layer. It doesn't do anything functional — it just makes the site findable by AI. Without it, everything else we built (the 8 tools, llms.txt, lead capture) is invisible to automated agents.

## Output quality check
Our current output is correct and follows the spec. One thing worth noting: the url field currently says http://localhost:3099/api/mcp — in production this would be the actual client domain (https://mixing.com/api/mcp). That's fine for the demo since it's derived from the request's Host header at runtime.

---

# llms.txt
What it does
llms.txt is a plain-text file served at the root of a website (https://mixing.com/llms.txt) that gives AI models a structured, machine-readable summary of the entire site — in a format they can consume directly without crawling or parsing HTML.

Our system generates two variants:

llms.txt — concise overview (link list + descriptions):

```
# Mixing Systems, Inc.

> Since its beginnings in 1985, Mixing Systems, Inc. has become a preferred provider...

Address: 7058 Corporate Way, Dayton, Ohio 45459, USA · Phone: +1 937-435-7227 · Email: amit@mixing.com

## Services
- [Food Processing Wastewater Treatment](https://mixing.com/feeds/service/food-processing-industry-wastewater-treatment-solutions): Transform your food and beverage facility's...
- [Expert Mixing Tank Solutions](https://mixing.com/feeds/service/mixing-tank-water-treatment): Transform your water treatment operations...

## Categories
- [Full Scale Hydraulic Mixers](https://mixing.com/feeds/category/full-scale-hydraulic-mixers): Full-scale hydraulic mixers provide efficient...

## Blog
- [Solving Activated Sludge Process Problems](https://mixing.com/feeds/blog/activated-sludge-process-problems-solutions)

## Products
- [Submersible Jet Aerators](lagoon aeration system)
- [Tank Mixing Systems](Tank mixing)
```

llms-full.txt — full markdown content of every page (what an AI would need to actually answer questions without visiting the site):

```
# Mixing Systems, Inc.

> Since its beginnings in 1985...

---

## Food Processing Wastewater Treatment Solutions
URL: https://mixing.com/feeds/service/food-processing-industry-wastewater-treatment-solutions

# Food Processing Wastewater Treatment Solutions

Transform your food and beverage facility's wastewater challenges...

## Overview
Our advanced jet aeration systems deliver...

## FAQ
**Q: What organic loads can the system handle?**
A: Our systems are designed for BOD levels up to...
```

## Why it exists
AI models (ChatGPT, Claude, Perplexity, Google AI) face a problem: they need to understand websites to recommend them in answers, but:
1. Crawling is expensive and slow — parsing HTML, removing nav/footer/ads, handling JS-rendered pages
2. HTML is noisy — the actual content is buried in layout markup
3. No standard format existed — every site structures content differently

llms.txt solves this the same way robots.txt solved crawler permissions and sitemap.xml solved page discovery. It's a convention: put a text file at a known path, in a known format, and AI systems can ingest your site instantly.

## The two files and when each is used

llms.txt(~2KB) -	
- Used By: AI crawlers doing broad indexing	
- Purpose: "What does this site cover?" Quick triage — enough to decide if a site is relevant to a query

llms-full.txt(~15-30KB) -	
- Used By: AI models generating answers	
- Purpose: "Give me the actual content so I can cite/quote it." The full knowledge base

An AI model answering "what companies make jet aeration systems?" would:

1. Find mixing.com/llms.txt → sees this site is about jet aeration ✓
2. Fetch mixing.com/llms-full.txt → reads the actual service descriptions, FAQs, specs
3. Include Mixing Systems in its answer with accurate details

## How it relates to MCP
llms.txt and MCP are two different distribution channels for the same data:

| Intent | llms.txt    |	MCP |
|--------|-------------|------|
| How AI accesses it |	Fetches a static file |	Calls tools interactively |
| Interaction model |	Read-only, one-shot |	Conversational, multi-turn |
| Best for |	Broad indexing, "know about this site" |	Deep queries, "search for X", "get page Y" |
| Analogy |	A brochure left at the door |	A salesperson who answers questions |

They complement each other. An AI model might discover the site via llms.txt during training/indexing, and later interact with it via MCP during a live conversation.

## Where it sits in the complete flow

[Sync]  →  [llms.txt generated]  →  AI crawlers fetch it passively
                                         │
[Sync]  →  [ai-index.json built] →  MCP tools query it actively
                                         │
Both serve the same content, different access patterns

Discovery:    "I have an MCP server"           (for live agents)
llms.txt:     "Here's my content as text"      (for crawlers/indexing)
Handshake:    "Let's talk"                     (for live agents)
Tools:        "Search, read, inquire"          (for live agents)

llms.txt is the passive visibility layer. Even if no MCP client ever connects, AI models that crawl the web will find and ingest this file. It's the lowest-friction way to get into AI-generated answers.

## Questions people typically ask
"Is this an official standard?"
It's a emerging convention proposed by the AI community (llmstxt.org). Not an RFC or W3C standard, but Anthropic, OpenAI, and search engines are increasingly checking for it. Early mover advantage — sites with llms.txt get cited more accurately than those without.

"Do AI models actually fetch this?"
Yes. Perplexity's crawler checks for it. ChatGPT's browsing tool looks for it. Claude's web search surfaces it. Google's AI overviews can use it. It's becoming the expected way to serve content to AI.

"Why markdown and not JSON?"
Because the consumer is a language model, not a program. LLMs understand markdown natively — headings, lists, bold, links. JSON would need parsing logic on the AI's side. Markdown is the LLM's native format.

"What's the difference between llms.txt and just having good SEO?"
SEO optimizes for search engine ranking. llms.txt optimizes for AI citation accuracy. A site can rank #1 on Google but get described incorrectly by ChatGPT because the AI couldn't extract clean content from the HTML. llms.txt gives the AI the exact words you want it to use.

"How often should it be regenerated?"
Every time site content changes. In our system, it regenerates on every Sync. In production, it would regenerate whenever the CMS publishes new content — same trigger as sitemap regeneration.

"What if the content is too long for llms-full.txt?"
For large sites (hundreds of pages), you'd typically include only the most important pages in full, and link to the rest. Our current implementation includes everything the content API returns, which is fine for SMB sites (10-200 pages). Enterprise sites would need pagination or selective inclusion.

"Why two files instead of one?"
Different AI access patterns have different context-window budgets. A crawler indexing 10,000 sites can only spare a few KB per site → llms.txt. An agent answering a specific user question about this site can afford the full content → llms-full.txt. Same pattern as sitemap.xml (index) vs actual pages (content).

---

# MCP Protocol Handshake (initialize + tools/list)

## What it does
After discovering the MCP server (via /.well-known/mcp), the AI agent sends two setup messages before it can do anything useful. Together, these form the "handshake."

### Step 1: initialize — negotiate protocol + capabilities

Client sends:
{
  "jsonrpc": "2.0",
  "id": 0,
  "method": "initialize",
  "params": { "protocolVersion": "2025-06-18" }
}

Server responds:
{
  "jsonrpc": "2.0",
  "id": 0,
  "result": {
    "protocolVersion": "2025-06-18",
    "capabilities": { "tools": { "listChanged": false } },
    "serverInfo": { "name": "Mixing Systems, Inc. site assistant", "version": "1.0.0" },
    "instructions": "Tools for querying Mixing Systems, Inc. (mixing.com): search pages, read page content as markdown, get business/contact info, list services and products, read customer reviews, and submit a sales inquiry."
  }
}

### Step 2: tools/list — enumerate available tools

Client sends:
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list",
  "params": {}
}

Server responds:
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "tools": [
      {
        "name": "search_site",
        "description": "Search this website's pages (services, product categories, blog articles). Returns ranked results with title, URL, and a content snippet.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "query": { "type": "string", "description": "Search query" },
            "limit": { "type": "number", "description": "Max results (default 5, max 20)" }
          },
          "required": ["query"]
        }
      },
      // ... 7 more tools
    ]
  }
}

Only AFTER both steps succeed can the client call tools/call to actually execute a tool.

## Why it exists (two steps, not one)

**Step 1 (initialize) answers: "Can we talk? Under what rules?"**
1. Version agreement — MCP evolves. The client says "I speak version 2025-06-18", the server agrees or downgrades to the highest mutually-supported version. This prevents breaking when either side upgrades.
2. Capability discovery — The server tells the client what it can do at a structural level. "tools": { "listChanged": false } means "I have tools, and they won't change mid-session." Future servers might advertise resources, prompts, or streaming capabilities here.
3. Instructions — A natural-language description of what this server is for. AI agents use this to decide when to call this server vs. others they might be connected to. It's like a system prompt for the server.

**Step 2 (tools/list) answers: "What exactly can I do here?"**
1. Tool enumeration — The client gets every tool's name, description, and full input schema (what parameters it accepts, which are required, types, constraints).
2. Schema-driven invocation — The AI agent uses these schemas to construct valid tool calls. Without this step, it would have to guess parameter names and types.
3. Dynamic discovery — Different servers expose different tools. A client connected to 5 servers learns each one's capabilities independently. It doesn't hardcode tool knowledge — it discovers it.

**Why not combine them into one message?**
Separation of concerns. Initialize is about the protocol and session. Tools/list is about functionality. A server could have zero tools (only resources) — initialize still works. Or a server could change tools mid-session (listChanged: true) — tools/list can be re-called without re-initializing.

## How it works in the flow

[Discovery]  →  [Handshake]  →  [Tool Calls]  →  [Lead Capture]
                     ▲
                YOU ARE HERE

"Does this site       Step 1: "What version?    "Search for X"   "Submit inquiry"
 speak AI?"                    What can you do?
                               Who are you?"
                      Step 2: "What tools do
                               you have? What
                               params do they need?"

The handshake is a trust + capability establishment step. Until BOTH succeed:
    - The client doesn't know which protocol version to use
    - The client doesn't know what capabilities are available
    - The client has no instructions on how to use this server
    - The client doesn't know what tools exist or how to call them

## What each initialize response field means
protocolVersion: The agreed-upon MCP spec version both sides will use for this session
capabilities.tools: "I have tools you can call"
capabilities.tools.listChanged: false = the tool list is static (won't add/remove tools mid-session)
serverInfo.name: Display name — includes the business name so the AI knows whose server this is
serverInfo.version: Server implementation version (for debugging/logging)
instructions: Natural language guidance — the AI agent uses this to understand scope and route requests

## Version agreement deep dive
The protocolVersion is the MCP specification version — maintained by Anthropic (who created the MCP standard). It's not our server version, not the client's version.

"2025-06-18" means "the MCP spec as defined on June 18, 2025." The spec defines: what methods exist (initialize, tools/list, tools/call), what fields each request/response has, what capabilities are valid, error codes, etc.

Why both sides need to agree: If the client speaks spec version 2025-06-18 but the server only understands 2024-11-05, they might disagree on field names, required params, or available methods. The handshake negotiation prevents this — the server picks the highest version both understand and that becomes the contract for the session.

## Why we don't show the endpoint URL in the handshake response
The client already knows the endpoint — it got it from the discovery document ("url": "http://localhost:3099/api/mcp"). And in MCP, there's no separate path per tool. ALL communication goes to that single endpoint via different JSON-RPC method values:

- initialize → handshake
- tools/list → enumerate tools
- tools/call → call a specific tool

It's one endpoint, many methods. The routing happens inside the JSON body (the "method" field), not in the URL path. This is a fundamental feature of JSON-RPC — one transport endpoint, multiplexed by method name.

## Why we show it in the demo (with Connect/Disconnect)
In the dashboard, the handshake card serves two purposes:
1. Proof of standards compliance — It shows this isn't a custom REST API. It's a real MCP server that any compliant client can connect to without custom integration.
2. Progressive disclosure gate — Connect triggers both steps (initialize → tools/list) and unlocks the Tool Playground below. Disconnect hides it. This mirrors reality: an AI agent that hasn't handshaked cannot call tools.
3. Protocol transparency — The raw JSON-RPC request/response pairs are shown for both steps, so viewers can see exactly what bytes go over the wire.

## Questions people typically ask
"Can I skip the handshake and just call tools directly?"
Technically our server doesn't enforce session state (it's stateless HTTP), so yes, a raw POST with tools/call would work. But compliant MCP clients always initialize first — it's how they discover capabilities and instructions. Skipping tools/list means the client would need hardcoded knowledge of tool names and schemas, defeating the purpose of a dynamic protocol.

"Why does tools/list return full JSON Schemas for each tool?"
So the AI can construct valid calls without guessing. The schema tells it: "search_site needs a required `query` string and an optional `limit` number." This is what enables AI agents to use tools they've never seen before — they read the schema, understand the interface, and build a correct invocation.

"What's JSON-RPC 2.0?"
The wire protocol MCP uses. It's a simple standard: every message has jsonrpc: "2.0", an id (for matching request/response), a method, and optional params. Same protocol Ethereum, LSP (VS Code language servers), and many other systems use. MCP chose it because it's transport-agnostic — works over HTTP, WebSocket, stdio.

"What does 'stateless' mean here?"
Our server doesn't maintain sessions between requests. Each POST is independent. This makes it simple to deploy (no session storage, no websocket connections) but means every request re-reads the index from disk. Real MCP also supports long-lived sessions over SSE/WebSocket — we chose stateless HTTP for simplicity and because our use case doesn't need streaming.

"Why does Claude Code make a GET /api/mcp call, and why does it get 405?"
This is expected behavior in the MCP Streamable HTTP transport spec. After initialize, the client attempts a GET to open an SSE (Server-Sent Events) stream — a long-lived connection where the server can push notifications to the client (like tools/listChanged, progress updates, etc.).

The full sequence is:
  POST initialize                → 200 (handshake)
  POST notifications/initialized → 202 (client tells server "I'm ready")
  GET  /api/mcp                  → 405 (probe for SSE stream)
  POST tools/list                → 200 (enumerate tools)

Our server returns 405 because we're stateless — we don't support server-initiated notifications. The client handles the 405 gracefully and moves on. This is explicitly allowed by the spec. We advertise `"tools": { "listChanged": false }` which means "my tools won't change mid-session, you don't need a notification channel."

We do NOT need to support this. If we ever wanted streaming (e.g., progress updates during a slow tool call), we'd implement a GET handler returning `text/event-stream`. For our use case — stateless, fixed tool set, fast responses — there's no benefit.

"What's listChanged: false?"
It tells the client "my list of tools won't change during our conversation." If this were true (set to true), the server could notify the client mid-session that new tools appeared (e.g., after an admin enables a feature). We don't need that — our 8 tools are fixed.

"Why does the server include instructions?"
Because an AI agent might be connected to 5 different MCP servers simultaneously. Instructions help it route: "This server handles mixing.com queries. That other server handles calendar. Don't ask this one about scheduling." It's context for the AI's tool-selection reasoning.

## Where it sits in the complete flow

Discovery:   "mixing.com has an MCP server at /api/mcp"
                              │
Handshake:   Step 1 — initialize                              ← YOU ARE HERE
             "I speak MCP 2025-06-18. I have tools.
              I'm the Mixing Systems site assistant."
                              │
             Step 2 — tools/list
             "Here are my 8 tools with full schemas:
              search_site, get_page, list_pages,
              get_business_info, list_services,
              list_products, get_reviews, submit_inquiry"
                              │
tools/call:  "search_site({query: 'jet aeration'})"
                              │
Lead:        "submit_inquiry({name, email, message})"

The handshake is the identity + capability + interface layer. Discovery says "I exist." Initialize says "Here's who I am." Tools/list says "Here's what I can do and how to ask." Tool calls are "Do it."

---

# ai-index.json

## What it does
ai-index.json is the internal structured data store that powers everything else in the AI Surface. It's a single JSON file that contains the complete knowledge graph of a project — pages, business info, services, products, reviews — in a normalized, queryable format.

It is NOT something external AI agents see or fetch. It's the engine behind the scenes:
- MCP tools query it (search_site, get_page, list_services, etc.)
- llms.txt is rendered from it
- llms-full.txt is rendered from it

## Why it exists
The content API returns raw project files (clusters, resources, project.json, reviews.json) in their original CMS format. ai-index.json transforms that into a single document optimized for tool queries — pre-extracted, pre-normalized, ready to search.

Without it, every MCP tool call would need to re-parse raw CMS data. With it, tools just read a clean structure: `index.pages[n].markdown`, `index.business.phones`, `index.services[n].description`.

## Why we show it in the demo
For transparency. The audience can see: "This is the raw data. And here are the 3 different ways it gets served to AI (MCP tools, llms.txt, llms-full.txt)." It answers the question: "Where does the content come from?"

## Where it sits in the flow

[Content API] → [Sync] → [ai-index.json] → MCP tools read it
                                           → llms.txt rendered from it
                                           → llms-full.txt rendered from it

It's the data layer. Everything above it is a presentation/access layer.

---

# Tool Playground (tools/call)

## What it does
After the handshake completes (`initialize` + `tools/list`), the AI agent knows what tools exist and their schemas. Now it can actually call them. The Tool Playground is a live demo of `tools/call` — the third and final JSON-RPC method in the MCP flow.

Each call is a real POST /api/mcp:
```
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": { "name": "search_site", "arguments": { "query": "jet aeration" } }
}
```

The server routes it to the matching handler, queries ai-index.json, and returns structured content.

## The 8 tools and their roles

They fall into three logical groups:

### Discovery tools — help the AI find what's on the site

| Tool | Purpose | Input |
|------|---------|-------|
| search_site | Keyword search across all pages (title weighted 5x, description 3x, body 1x per hit, phrase bonus). Returns ranked results with snippets. | query (required), limit (optional, max 20) |
| list_pages | Browse all pages, optionally filtered by type (blog, category, service, topics, page). | type (optional enum) |
| get_page | Fetch the full markdown content of a specific page by its path. Omit or empty for homepage. | path (optional, default: homepage) |

### Entity tools — structured data about the business

| Tool | Purpose | Input |
|------|---------|-------|
| get_business_info | Returns name, address, phones, emails, hours, service areas, certifications, about text. | none |
| list_services | All services with descriptions. | none |
| list_products | All products with attributes. Optional keyword filter. | query (optional) |
| get_reviews | Customer testimonials. | none |

### Action tool — the only one that writes/mutates

| Tool | Purpose | Input |
|------|---------|-------|
| submit_inquiry | Submit a lead/contact form on behalf of the user. POSTs to the prod lead API. | name, email, message (required); phone, page_path (optional) |

## How search works (search_site)

search_site uses a lightweight TF-based scorer (no external search engine):

1. Tokenize the query into lowercase alphanumeric words
2. For each page, score by: title match (+5 per token), description match (+3), body occurrences (+1 each, capped at 5 per token)
3. Exact phrase match bonus: +8 in title, +4 in body
4. Sort by score, return top N with a snippet (context window around the first match)

No vector embeddings, no external service. It's simple keyword scoring against the ai-index.json — fast, predictable, zero dependencies.

## How submit_inquiry works (the lead capture path)

This is the monetization hook. When an AI agent is talking to a user who says "I want to contact them about jet aeration systems," the agent calls submit_inquiry. It:

1. Validates: name required, email must match a regex, message required (max 2000 chars)
2. Looks up the page_path in the index (for product attribution)
3. POSTs to https://api.gushwork.ai/seo-v2/lead with:
   - request_origin: the project's actual domain (e.g., mixing.com)
   - leads_info: name, email, phone, message
   - form_details: tagged as "MCP" request type, includes the product name if applicable

Same endpoint that the website's contact form uses. The business gets a real lead in their CRM — they don't know (or need to know) it came through an AI agent.

## Why this set of tools?

It mirrors what an AI agent would naturally want to do when answering questions about a business:

1. "Does this company do X?" → search_site or list_services
2. "Tell me about their products" → list_products
3. "Give me details on this specific service" → get_page
4. "What's their contact info?" → get_business_info
5. "Are they well-reviewed?" → get_reviews
6. "Put me in touch" → submit_inquiry

It's the full lifecycle: discover → learn → decide → act.

## Questions people typically ask

"Why not just one search tool?"
Because AI agents reason better with specialized tools. An agent asking "what products do they have" should hit list_products (structured, filtered) rather than search_site (full-text, noisy). The schema descriptions guide the AI to pick the right tool for the job.

"Why are tool responses plain text, not JSON?"
MCP tool responses use a content array with typed blocks (text, image, etc.). We return text because the consumer is an AI model — it reads text natively. Structured JSON inside the text (like get_business_info returns) is fine because the AI can parse it. No need for a separate structured format when the reader is a language model.

"Can an AI agent call these tools without seeing our code?"
Yes — that's the entire point of tools/list. The agent gets the schema (name, description, inputSchema with types and required fields), constructs a valid call, and interprets the text response. Zero integration code needed on the client side. Any MCP-compliant client works out of the box.

"Is submit_inquiry safe? Could an AI spam leads?"
The description says "Only use when the user explicitly asks to contact the business, and confirm the details with them first." Responsible AI agents (Claude, ChatGPT) follow tool descriptions as behavioral instructions. Plus: email validation, message length cap, and the backend has its own rate limits.

"How does an AI agent collect user info for submit_inquiry?"
It doesn't fire the tool blindly. The flow is conversational:

  User:  "I'm interested in their jet aeration systems, can you put me in touch?"
  AI:    [reads submit_inquiry schema: name, email, message required]
  AI:    "I'd be happy to help. I'll need your name, email, and a brief message."
  User:  "John Smith, john@acme.com, interested in the JA-100 for our plant"
  AI:    "Just to confirm — submit inquiry with Name: John Smith, Email: john@acme.com,
          Message: 'Interested in the JA-100 for our wastewater plant.' Send it?"
  User:  "Yes"
  AI:    [NOW calls tools/call → submit_inquiry with those values]
  AI:    "Done! They'll respond to john@acme.com."

The tool description acts as a behavioral instruction to the AI. "Confirm the details with them first" isn't code enforcement — it's a prompt-level guardrail that responsible LLMs follow. The AI mediates between the user and the tool. In our demo playground, we require email and message as mandatory inputs to simulate this flow.

"Why no authentication on tool calls?"
Same reason as the discovery document: intentionally public. The whole value prop is that ANY AI agent can discover, query, and interact with the site. Authentication would defeat the purpose — you want Claude/Perplexity/ChatGPT to call your tools without friction.

"What happens if the index isn't loaded?"
Every tool (except submit_inquiry) is wrapped in a requireIndex guard. If ai-index.json hasn't been built yet, tools return: "This site's AI index is not available yet. Try the website directly." Graceful degradation, not a crash.

"Why doesn't tools/list have a GET or separate URL path?"
Because MCP uses JSON-RPC over a single endpoint. ALL communication is POST /api/mcp — the routing happens inside the JSON body via the "method" field:

  POST /api/mcp  →  {"method": "initialize"}     ← handshake
  POST /api/mcp  →  {"method": "tools/list"}     ← enumerate tools
  POST /api/mcp  →  {"method": "tools/call"}     ← execute a tool

This is how JSON-RPC works — one transport endpoint, multiplexed by the method string in the payload. There's no /api/mcp/tools/list URL or GET /api/mcp?action=list. The server reads msg.method to dispatch internally. It's like having one phone number (the endpoint) and saying different things when they pick up (the method). As opposed to REST, where you'd have different URLs + HTTP verbs for each action.

## Where it sits in the complete flow

```
Discovery:    "I have an MCP server"                (/.well-known/mcp)
Handshake:    "I speak v2025-06-18, I have tools"   (initialize)
Enumeration:  "Here are 8 tools + schemas"          (tools/list)
Execution:    "search_site({query: '...'})"         (tools/call)  ← YOU ARE HERE
Lead:         "submit_inquiry({name, email, msg})"  (tools/call → prod API)
```

The Tool Playground proves the whole stack is live: real content, real search, real lead capture — all through a standards-compliant protocol that any AI agent speaks natively.

---

# How to Demo?
1. Run claude mcp add mixing-systems --transport http http://localhost:3099/api/mcp
2. Ask some questions specific to mixing-system from inside claude code.
3. It should produce output logs as follows:
```
[MCP] 2026-07-10T23:18:16.356Z | initialize | protocolVersion:2025-11-25 | client:Claude Code | ip:::1 | 0ms | ua:claude-code/2.1.183 (cli)
 POST /api/mcp 200 in 211ms
[MCP] 2026-07-10T23:18:16.364Z | notifications/initialized | notification (no response) | client:Claude Code | ip:::1 | 0ms | ua:claude-code/2.1.183 (cli)
 POST /api/mcp 202 in 4ms
[MCP] 2026-07-10T23:18:16.369Z | GET | rejected — must use POST | client:Claude Code | ip:::1 | ua:claude-code/2.1.183 (cli)
 GET /api/mcp 405 in 5ms
[MCP] 2026-07-10T23:18:16.370Z | tools/list | enumerate tools | client:Claude Code | ip:::1 | 0ms | ua:claude-code/2.1.183 (cli)
 POST /api/mcp 200 in 3ms
[MCP] 2026-07-10T23:18:56.895Z | initialize | protocolVersion:2025-11-25 | client:Claude Code | ip:::1 | 0ms | ua:claude-code/2.1.183 (cli)
 POST /api/mcp 200 in 3ms
[MCP] 2026-07-10T23:18:56.908Z | notifications/initialized | notification (no response) | client:Claude Code | ip:::1 | 0ms | ua:claude-code/2.1.183 (cli)
 POST /api/mcp 202 in 5ms
[MCP] 2026-07-10T23:18:56.914Z | GET | rejected — must use POST | client:Claude Code | ip:::1 | ua:claude-code/2.1.183 (cli)
 GET /api/mcp 405 in 5ms
[MCP] 2026-07-10T23:18:56.915Z | tools/list | enumerate tools | client:Claude Code | ip:::1 | 0ms | ua:claude-code/2.1.183 (cli)
 POST /api/mcp 200 in 6ms
[MCP] 2026-07-10T23:25:37.535Z | tools/call → get_business_info | args:{} | client:Claude Code | ip:::1 | 0ms | ua:claude-code/2.1.183 (cli)
 POST /api/mcp 200 in 7ms
[MCP] 2026-07-10T23:26:03.160Z | tools/call → list_products | args:{} | client:Claude Code | ip:::1 | 0ms | ua:claude-code/2.1.183 (cli)
 POST /api/mcp 200 in 6ms
[MCP] 2026-07-10T23:26:05.658Z | tools/call → list_services | args:{} | client:Claude Code | ip:::1 | 0ms | ua:claude-code/2.1.183 (cli)
 POST /api/mcp 200 in 6ms
```