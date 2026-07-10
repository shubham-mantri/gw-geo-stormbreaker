"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  FileJson,
  FileText,
  Globe,
  Loader2,
  Plug,
  PlugZap,
  RefreshCw,
  Send,
  Server,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";

type Project = { id: string; name: string; url: string };

type SyncStats = {
  files: number;
  pages: number;
  products: number;
  services: number;
  reviews: number;
};

type ToolResult = {
  tool: string;
  args: Record<string, unknown>;
  response: unknown;
  latency: number;
};

// ── Combobox Dropdown ───────────────────────────────────────────────────────

function ProjectCombobox({
  projects,
  value,
  onChange,
}: {
  projects: Project[];
  value: string;
  onChange: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const filtered = filter
    ? projects.filter(
        (p) =>
          p.name.toLowerCase().includes(filter.toLowerCase()) ||
          p.url.toLowerCase().includes(filter.toLowerCase()),
      )
    : projects;

  const selected = projects.find((p) => p.id === value);

  return (
    <div ref={ref} className="relative flex-1">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between rounded-md border bg-background px-3 py-2 text-sm hover:bg-accent"
      >
        <span className={selected ? "text-foreground" : "text-muted-foreground"}>
          {selected ? `${selected.name} — ${selected.url}` : "Select a project..."}
        </span>
        <ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-full rounded-md border bg-popover shadow-lg">
          <div className="p-2">
            <Input
              placeholder="Search projects..."
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              autoFocus
            />
          </div>
          <div className="max-h-60 overflow-y-auto">
            {filtered.slice(0, 100).map((p) => (
              <button
                key={p.id}
                type="button"
                className={`w-full px-3 py-2 text-left text-sm hover:bg-accent ${p.id === value ? "bg-accent font-medium" : ""}`}
                onClick={() => {
                  onChange(p.id);
                  setOpen(false);
                  setFilter("");
                }}
              >
                <span className="font-medium">{p.name}</span>
                <span className="ml-2 text-muted-foreground">{p.url}</span>
              </button>
            ))}
            {filtered.length === 0 && (
              <p className="px-3 py-2 text-sm text-muted-foreground">No projects found</p>
            )}
            {filtered.length > 100 && (
              <p className="px-3 py-2 text-xs text-muted-foreground">
                Showing 100 of {filtered.length} — type to narrow
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Project Selector + Sync ─────────────────────────────────────────────────

function ProjectSync({
  onSynced,
  onSyncStart,
}: {
  onSynced: (stats: SyncStats, project: Project) => void;
  onSyncStart: () => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastSynced, setLastSynced] = useState<Project | null>(null);

  useEffect(() => {
    fetch("/api/projects")
      .then((r) => r.json())
      .then((data) => setProjects(data))
      .catch(() => setError("Failed to load projects"));
  }, []);

  const handleSync = useCallback(async () => {
    if (!selected) return;
    setSyncing(true);
    setError(null);
    onSyncStart();
    try {
      const res = await fetch("/api/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ projectId: selected }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Sync failed");
        return;
      }
      const proj = projects.find((p) => p.id === selected)!;
      setLastSynced(proj);
      onSynced(data.stats, proj);
    } catch {
      setError("Network error during sync");
    } finally {
      setSyncing(false);
    }
  }, [selected, projects, onSynced, onSyncStart]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Project Sync</CardTitle>
        <CardDescription>
          Select a project and sync its content from the database. This builds
          the MCP index, llms.txt, and all AI surface files in real time.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-2">
          <ProjectCombobox
            projects={projects}
            value={selected}
            onChange={setSelected}
          />
          <Button
            onClick={handleSync}
            disabled={!selected || syncing}
          >
            {syncing ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="mr-2 h-4 w-4" />
            )}
            {syncing ? "Syncing..." : "Sync"}
          </Button>
        </div>

        {error && (
          <p className="text-sm text-red-600">{error}</p>
        )}
        {lastSynced && !syncing && (
          <div className="flex items-center gap-2 text-sm text-green-700">
            <CheckCircle2 className="h-4 w-4" />
            Synced: {lastSynced.name}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── MCP Handshake ───────────────────────────────────────────────────────────

function McpHandshake({
  connected,
  onConnect,
  onDisconnect,
}: {
  connected: boolean;
  onConnect: (result: unknown, tools: unknown[]) => void;
  onDisconnect: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [initReq, setInitReq] = useState<Record<string, unknown> | null>(null);
  const [initRes, setInitRes] = useState<Record<string, unknown> | null>(null);
  const [toolsReq, setToolsReq] = useState<Record<string, unknown> | null>(null);
  const [toolsRes, setToolsRes] = useState<Record<string, unknown> | null>(null);
  const [activeTab, setActiveTab] = useState<"requests" | "playground">("requests");

  const handleConnect = useCallback(async () => {
    setLoading(true);
    const initBody = { jsonrpc: "2.0", id: 0, method: "initialize", params: { protocolVersion: "2025-06-18" } };
    setInitReq(initBody);
    const res1 = await fetch("/api/mcp", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(initBody) });
    const initData = await res1.json();
    setInitRes(initData);

    const toolsBody = { jsonrpc: "2.0", id: 1, method: "tools/list", params: {} };
    setToolsReq(toolsBody);
    const res2 = await fetch("/api/mcp", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(toolsBody) });
    const toolsData = await res2.json();
    setToolsRes(toolsData);

    setLoading(false);
    setActiveTab("requests");
    onConnect(initData.result, toolsData.result?.tools ?? []);
  }, [onConnect]);

  const handleDisconnect = useCallback(() => {
    setInitReq(null);
    setInitRes(null);
    setToolsReq(null);
    setToolsRes(null);
    setActiveTab("requests");
    onDisconnect();
  }, [onDisconnect]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          {connected ? <PlugZap className="h-4 w-4 text-green-600" /> : <Plug className="h-4 w-4" />}
          MCP Protocol
        </CardTitle>
        <CardDescription>
          {connected
            ? "Connected — view the handshake exchange or test tools live."
            : "Connect to negotiate protocol version, discover capabilities, and enumerate tools."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-3">
          {!connected ? (
            <Button onClick={handleConnect} disabled={loading}>
              {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plug className="mr-2 h-4 w-4" />}
              {loading ? "Connecting..." : "Connect"}
            </Button>
          ) : (
            <Button variant="destructive" size="sm" onClick={handleDisconnect}>
              <PlugZap className="mr-2 h-4 w-4" />
              Disconnect
            </Button>
          )}
        </div>

        {connected && (
          <>
            <div className="flex gap-1 rounded-lg bg-muted p-1">
              <button
                className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${activeTab === "requests" ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                onClick={() => setActiveTab("requests")}
              >
                View Requests
              </button>
              <button
                className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${activeTab === "playground" ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
                onClick={() => setActiveTab("playground")}
              >
                Tool Playground
              </button>
            </div>

            {activeTab === "requests" && (
              <div className="space-y-4">
                <div className="space-y-2">
                  <p className="text-xs font-semibold">Step 1 — initialize</p>
                  {initReq && (
                    <div className="space-y-2">
                      <div>
                        <p className="mb-1 text-xs text-muted-foreground">→ Request</p>
                        <pre className="whitespace-pre-wrap break-words rounded-md border bg-muted/50 p-3 text-xs">{JSON.stringify(initReq, null, 2)}</pre>
                      </div>
                      {initRes && (
                        <div>
                          <p className="mb-1 text-xs text-muted-foreground">← Response</p>
                          <pre className="whitespace-pre-wrap break-words rounded-md border bg-green-50 p-3 text-xs text-green-900">{JSON.stringify(initRes, null, 2)}</pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
                <div className="space-y-2">
                  <p className="text-xs font-semibold">Step 2 — tools/list</p>
                  {toolsReq && (
                    <div className="space-y-2">
                      <div>
                        <p className="mb-1 text-xs text-muted-foreground">→ Request</p>
                        <pre className="whitespace-pre-wrap break-words rounded-md border bg-muted/50 p-3 text-xs">{JSON.stringify(toolsReq, null, 2)}</pre>
                      </div>
                      {toolsRes && (
                        <div>
                          <p className="mb-1 text-xs text-muted-foreground">← Response ({((toolsRes.result as Record<string, unknown[]>)?.tools?.length) ?? 0} tools)</p>
                          <pre className="whitespace-pre-wrap break-words rounded-md border bg-green-50 p-3 text-xs text-green-900 max-h-64 overflow-y-auto">{JSON.stringify(toolsRes, null, 2)}</pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}

            {activeTab === "playground" && <ToolPlayground />}
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ── MCP Tool Playground ─────────────────────────────────────────────────────

type ToolDef = {
  name: string;
  description: string;
  inputSchema: {
    type: string;
    properties: Record<string, { type: string; description?: string; enum?: string[] }>;
    required?: string[];
  };
};

const TOOLS: ToolDef[] = [
  { name: "search_site", description: "Search this website's pages. Returns ranked results with title, URL, and snippet.", inputSchema: { type: "object", properties: { query: { type: "string", description: "Search query" }, limit: { type: "number", description: "Max results (default 5, max 20)" } }, required: ["query"] } },
  { name: "get_page", description: "Get full page content as markdown by path. Omit or empty for homepage.", inputSchema: { type: "object", properties: { path: { type: "string", description: "Page path (e.g. 'blog/my-post'). Empty = homepage." } } } },
  { name: "list_pages", description: "List all pages with title, URL, description. Optionally filter by type.", inputSchema: { type: "object", properties: { type: { type: "string", enum: ["blog", "category", "service", "topics", "page"], description: "Optional page type filter" } } } },
  { name: "get_business_info", description: "Get structured business info: name, address, phone, email, hours, service areas, certifications.", inputSchema: { type: "object", properties: {} } },
  { name: "list_services", description: "List services this business offers, with descriptions.", inputSchema: { type: "object", properties: {} } },
  { name: "list_products", description: "List products with attributes. Optionally filter by query.", inputSchema: { type: "object", properties: { query: { type: "string", description: "Optional filter on product names/attributes" } } } },
  { name: "get_reviews", description: "Get customer reviews/testimonials.", inputSchema: { type: "object", properties: {} } },
  { name: "submit_inquiry", description: "Submit a sales/contact inquiry on behalf of the user.", inputSchema: { type: "object", properties: { name: { type: "string", description: "User's name" }, email: { type: "string", description: "User's email" }, phone: { type: "string", description: "Phone (optional)" }, message: { type: "string", description: "What the user needs (max 2000 chars)" }, page_path: { type: "string", description: "Page/product path (optional)" } }, required: ["name", "email", "message"] } },
];

async function callMcpTool(
  toolName: string,
  args: Record<string, unknown>,
): Promise<{ result: unknown; latency: number }> {
  const start = performance.now();
  const res = await fetch("/api/mcp", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: toolName, arguments: args } }),
  });
  const data = await res.json();
  return { result: data.result, latency: Math.round(performance.now() - start) };
}

function ToolPlayground() {
  const [activeTool, setActiveTool] = useState<string>(TOOLS[0].name);
  const [fieldValues, setFieldValues] = useState<Record<string, Record<string, string>>>({});
  const [results, setResults] = useState<ToolResult[]>([]);
  const [loading, setLoading] = useState(false);

  const tool = TOOLS.find((t) => t.name === activeTool)!;
  const fields = Object.entries(tool.inputSchema.properties);
  const required = tool.inputSchema.required ?? [];
  const values = fieldValues[activeTool] ?? {};

  const setField = useCallback((field: string, value: string) => {
    setFieldValues((prev) => ({
      ...prev,
      [activeTool]: { ...(prev[activeTool] ?? {}), [field]: value },
    }));
  }, [activeTool]);

  const canTest = required.every((f) => (values[f] ?? "").trim().length > 0);

  const runTest = useCallback(async () => {
    const args: Record<string, unknown> = {};
    for (const [key] of fields) {
      const v = (values[key] ?? "").trim();
      if (v) args[key] = v;
    }
    setLoading(true);
    const { result, latency } = await callMcpTool(activeTool, args);
    setResults((prev) => [{ tool: activeTool, args, response: result, latency }, ...prev]);
    setLoading(false);
  }, [activeTool, fields, values]);

  return (
    <div className="space-y-4">
      {/* Tool tabs */}
      <div className="flex flex-wrap gap-1.5">
        {TOOLS.map((t) => (
          <button
            key={t.name}
            className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${activeTool === t.name ? "border-primary bg-primary text-primary-foreground" : "border-border bg-background text-muted-foreground hover:text-foreground hover:border-primary/50"}`}
            onClick={() => setActiveTool(t.name)}
          >
            {t.name}
          </button>
        ))}
      </div>

      {/* Active tool detail */}
      <div className="rounded-md border p-4 space-y-3">
        <div>
          <p className="text-sm font-medium">{tool.name}</p>
          <p className="text-xs text-muted-foreground">{tool.description}</p>
        </div>

        {fields.length > 0 ? (
          <div className="space-y-2">
            {fields.map(([key, schema]) => {
              const isRequired = required.includes(key);
              return (
                <div key={key} className="space-y-1">
                  <label className="flex items-center gap-1.5 text-xs font-medium">
                    {key}
                    {isRequired ? (
                      <span className="rounded bg-red-100 px-1 py-0.5 text-[10px] font-semibold text-red-700">required</span>
                    ) : (
                      <span className="rounded bg-gray-100 px-1 py-0.5 text-[10px] text-gray-500">optional</span>
                    )}
                  </label>
                  {schema.enum ? (
                    <select
                      className="w-full rounded-md border bg-background px-3 py-1.5 text-sm"
                      value={values[key] ?? ""}
                      onChange={(e) => setField(key, e.target.value)}
                    >
                      <option value="">— select —</option>
                      {schema.enum.map((v) => (
                        <option key={v} value={v}>{v}</option>
                      ))}
                    </select>
                  ) : (
                    <Input
                      value={values[key] ?? ""}
                      onChange={(e) => setField(key, e.target.value)}
                      placeholder={schema.description ?? key}
                      onKeyDown={(e) => e.key === "Enter" && canTest && !loading && runTest()}
                    />
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground italic">No parameters — this tool takes no input.</p>
        )}

        <Button onClick={runTest} disabled={loading || !canTest} size="sm">
          {loading ? <Loader2 className="mr-2 h-3 w-3 animate-spin" /> : <Send className="mr-2 h-3 w-3" />}
          {loading ? "Running..." : "Test"}
        </Button>
      </div>

      {/* Results */}
      {results.length > 0 && (
        <div className="space-y-3">
          {results.map((r, i) => (
            <div key={i} className="rounded-md border bg-muted/50 p-3">
              <div className="mb-2 flex items-center justify-between">
                <code className="text-xs font-semibold text-primary">tools/call → {r.tool}</code>
                <span className="text-xs text-muted-foreground">{r.latency}ms</span>
              </div>
              {Object.keys(r.args).length > 0 && (
                <pre className="mb-2 whitespace-pre-wrap text-xs text-muted-foreground">{JSON.stringify(r.args, null, 2)}</pre>
              )}
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs text-foreground/80">
                {typeof r.response === "string" ? r.response : JSON.stringify(r.response, null, 2)}
              </pre>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── llms.txt Viewer ─────────────────────────────────────────────────────────

function LlmsTxtViewer() {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState<"llms.txt" | "llms-full.txt" | null>(null);

  const loadFile = useCallback(
    async (f: "llms.txt" | "llms-full.txt") => {
      if (active === f) {
        setActive(null);
        setContent(null);
        return;
      }
      setActive(f);
      setLoading(true);
      const res = await fetch(`/${f}`);
      setContent(await res.text());
      setLoading(false);
    },
    [active],
  );

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <Button
          variant={active === "llms.txt" ? "default" : "outline"}
          size="sm"
          onClick={() => loadFile("llms.txt")}
          disabled={loading}
        >
          <FileText className="mr-1 h-3 w-3" />
          llms.txt
        </Button>
        <Button
          variant={active === "llms-full.txt" ? "default" : "outline"}
          size="sm"
          onClick={() => loadFile("llms-full.txt")}
          disabled={loading}
        >
          <FileText className="mr-1 h-3 w-3" />
          llms-full.txt
        </Button>
      </div>
      {active && content !== null && (
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted/50 p-4 text-xs leading-relaxed">
          {content}
        </pre>
      )}
    </div>
  );
}

// ── MCP Discovery ───────────────────────────────────────────────────────────

function McpDiscovery() {
  const [content, setContent] = useState<Record<string, unknown> | null>(null);
  const [active, setActive] = useState(false);
  const [loading, setLoading] = useState(false);

  const toggle = useCallback(async () => {
    if (active) {
      setActive(false);
      setContent(null);
      return;
    }
    setLoading(true);
    const res = await fetch("/.well-known/mcp");
    setContent(await res.json());
    setActive(true);
    setLoading(false);
  }, [active]);

  return (
    <div className="space-y-3">
      <Button variant={active ? "default" : "outline"} size="sm" onClick={toggle} disabled={loading}>
        <Globe className="mr-1 h-3 w-3" />
        {active ? "Hide" : "View"} /.well-known/mcp
      </Button>
      {active && content && (
        <pre className="whitespace-pre-wrap break-words rounded-md border bg-muted/50 p-4 text-xs">
          {JSON.stringify(content, null, 2)}
        </pre>
      )}
    </div>
  );
}

function AiIndexViewer() {
  const [content, setContent] = useState<Record<string, unknown> | null>(null);
  const [active, setActive] = useState(false);
  const [loading, setLoading] = useState(false);

  const toggle = useCallback(async () => {
    if (active) {
      setActive(false);
      setContent(null);
      return;
    }
    setLoading(true);
    const res = await fetch("/ai-index.json");
    setContent(await res.json());
    setActive(true);
    setLoading(false);
  }, [active]);

  return (
    <div className="space-y-3">
      <Button variant={active ? "default" : "outline"} size="sm" onClick={toggle} disabled={loading}>
        <FileJson className="mr-1 h-3 w-3" />
        {active ? "Hide" : "View"} ai-index.json
      </Button>
      {active && content && (
        <pre className="whitespace-pre-wrap break-words rounded-md border bg-muted/50 p-4 text-xs max-h-96 overflow-y-auto">
          {JSON.stringify(content, null, 2)}
        </pre>
      )}
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────────────────────

type FoldId = "mcp-endpoint" | "llms-txt" | "mcp-discovery" | "ai-index";

function StatusCard({
  icon: Icon,
  title,
  path,
  live,
  foldId,
  activeFold,
  onSelect,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  path: string;
  live: boolean;
  foldId: FoldId;
  activeFold: FoldId | null;
  onSelect: (id: FoldId) => void;
}) {
  const isActive = activeFold === foldId;
  return (
    <Card
      className={`cursor-pointer transition-colors hover:border-primary/50 ${isActive ? "border-primary ring-1 ring-primary/30" : ""}`}
      onClick={() => onSelect(foldId)}
    >
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm font-medium">
          <Icon className="h-4 w-4" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-center justify-between">
          <code className="text-xs text-muted-foreground">{path}</code>
          {live ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-green-100 px-2.5 py-0.5 text-xs font-medium text-green-800">
              <span className="h-1.5 w-1.5 rounded-full bg-green-500" />
              Live
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-gray-600">
              <span className="h-1.5 w-1.5 rounded-full bg-gray-400" />
              Unavailable
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export default function AiSurfacePage() {
  const [syncStats, setSyncStats] = useState<SyncStats | null>(null);
  const [syncedProject, setSyncedProject] = useState<Project | null>(null);
  const [synced, setSynced] = useState(false);
  const [connected, setConnected] = useState(false);
  const [activeFold, setActiveFold] = useState<FoldId | null>(null);

  const foldRefs = {
    "mcp-endpoint": useRef<HTMLDivElement>(null),
    "llms-txt": useRef<HTMLDivElement>(null),
    "mcp-discovery": useRef<HTMLDivElement>(null),
    "ai-index": useRef<HTMLDivElement>(null),
  };

  const handleFoldSelect = useCallback((id: FoldId) => {
    setActiveFold((prev) => {
      const next = prev === id ? null : id;
      if (next) {
        setTimeout(() => {
          foldRefs[id]?.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        }, 50);
      }
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSyncStart = useCallback(() => {
    setSynced(false);
    setSyncStats(null);
    setSyncedProject(null);
    setConnected(false);
  }, []);

  const handleSynced = useCallback((stats: SyncStats, project: Project) => {
    setSyncStats(stats);
    setSyncedProject(project);
    setSynced(true);
  }, []);

  const handleConnect = useCallback((_result: unknown, _tools: unknown[]) => {
    setConnected(true);
  }, []);

  const handleDisconnect = useCallback(() => {
    setConnected(false);
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">AI Surface</h1>
        <p className="text-sm text-muted-foreground">
          Open MCP server + llms.txt for AI discoverability — select a project,
          sync its content, and the AI surface goes live instantly.
        </p>
      </div>

      {/* Step 1: Project Sync */}
      <ProjectSync onSynced={handleSynced} onSyncStart={handleSyncStart} />

      {/* Step 2: After sync — show stats, endpoints, + handshake */}
      {synced && syncStats && syncedProject && (
        <>
          {/* Stats row */}
          <div className="grid gap-4 sm:grid-cols-5">
            <Card>
              <CardContent className="pt-4">
                <p className="text-2xl font-bold">{syncStats.pages}</p>
                <p className="text-xs text-muted-foreground">Pages</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4">
                <p className="text-2xl font-bold">{syncStats.services}</p>
                <p className="text-xs text-muted-foreground">Services</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4">
                <p className="text-2xl font-bold">{syncStats.products}</p>
                <p className="text-xs text-muted-foreground">Products</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4">
                <p className="text-2xl font-bold">{syncStats.reviews}</p>
                <p className="text-xs text-muted-foreground">Reviews</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4">
                <p className="text-2xl font-bold">{syncStats.files}</p>
                <p className="text-xs text-muted-foreground">Raw Files</p>
              </CardContent>
            </Card>
          </div>

          {/* Endpoint status cards — clickable, scroll to fold */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatusCard icon={FileJson} title="ai-index.json" path="/ai-index.json" live foldId="ai-index" activeFold={activeFold} onSelect={handleFoldSelect} />
            <StatusCard icon={FileText} title="llms.txt" path="/llms.txt" live foldId="llms-txt" activeFold={activeFold} onSelect={handleFoldSelect} />
            <StatusCard icon={Globe} title="MCP Discovery" path="/.well-known/mcp" live foldId="mcp-discovery" activeFold={activeFold} onSelect={handleFoldSelect} />
            <StatusCard icon={Server} title="MCP Endpoint" path="/api/mcp" live={connected} foldId="mcp-endpoint" activeFold={activeFold} onSelect={handleFoldSelect} />
          </div>

          {/* Folds — shown when the corresponding card is active */}
          {activeFold === "mcp-discovery" && (
            <div ref={foldRefs["mcp-discovery"]}>
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">MCP Discovery Document</CardTitle>
                  <CardDescription>
                    GET /.well-known/mcp — the equivalent of robots.txt for AI agents.
                    Any MCP client checks this URL to discover available servers.
                    This includes {syncedProject.name}&apos;s info.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <McpDiscovery />
                </CardContent>
              </Card>
            </div>
          )}

          {activeFold === "llms-txt" && (
            <div ref={foldRefs["llms-txt"]}>
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">llms.txt</CardTitle>
                  <CardDescription>
                    Machine-readable site overview for AI crawlers. Click to view the
                    generated content for {syncedProject.name}.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <LlmsTxtViewer />
                </CardContent>
              </Card>
            </div>
          )}

          {activeFold === "ai-index" && (
            <div ref={foldRefs["ai-index"]}>
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">ai-index.json</CardTitle>
                  <CardDescription>
                    The structured data store that powers MCP tools and llms.txt.
                    This is the internal representation built from {syncedProject.name}&apos;s
                    content — every tool query reads from this.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <AiIndexViewer />
                </CardContent>
              </Card>
            </div>
          )}

          <div ref={foldRefs["mcp-endpoint"]} className={activeFold === "mcp-endpoint" ? "" : "hidden"}>
            <McpHandshake
              connected={connected}
              onConnect={handleConnect}
              onDisconnect={handleDisconnect}
            />
          </div>

        </>
      )}
    </div>
  );
}
