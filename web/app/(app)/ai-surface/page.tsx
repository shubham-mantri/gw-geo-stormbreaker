"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  FileJson,
  FileText,
  Globe,
  Loader2,
  Plug,
  PlugZap,
  RefreshCw,
  Search,
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

  const handleConnect = useCallback(async () => {
    setLoading(true);

    // Step 1: initialize
    const initBody = {
      jsonrpc: "2.0",
      id: 0,
      method: "initialize",
      params: { protocolVersion: "2025-06-18" },
    };
    setInitReq(initBody);
    const res1 = await fetch("/api/mcp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(initBody),
    });
    const initData = await res1.json();
    setInitRes(initData);

    // Step 2: tools/list
    const toolsBody = {
      jsonrpc: "2.0",
      id: 1,
      method: "tools/list",
      params: {},
    };
    setToolsReq(toolsBody);
    const res2 = await fetch("/api/mcp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(toolsBody),
    });
    const toolsData = await res2.json();
    setToolsRes(toolsData);

    setLoading(false);
    onConnect(initData.result, toolsData.result?.tools ?? []);
  }, [onConnect]);

  const handleDisconnect = useCallback(() => {
    setInitReq(null);
    setInitRes(null);
    setToolsReq(null);
    setToolsRes(null);
    onDisconnect();
  }, [onDisconnect]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          {connected ? (
            <PlugZap className="h-4 w-4 text-green-600" />
          ) : (
            <Plug className="h-4 w-4" />
          )}
          MCP Protocol Handshake
        </CardTitle>
        <CardDescription>
          The first two messages any MCP client sends — (1) negotiate protocol
          version and discover capabilities, then (2) enumerate available tools.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!connected ? (
          <Button onClick={handleConnect} disabled={loading}>
            {loading ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Plug className="mr-2 h-4 w-4" />
            )}
            {loading ? "Connecting..." : "Connect"}
          </Button>
        ) : (
          <Button variant="destructive" onClick={handleDisconnect}>
            <PlugZap className="mr-2 h-4 w-4" />
            Disconnect
          </Button>
        )}

        {/* Step 1: initialize */}
        {initReq && (
          <div className="space-y-3">
            <p className="text-xs font-semibold text-muted-foreground">
              Step 1 — initialize
            </p>
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">
                → Client Request (POST /api/mcp)
              </p>
              <pre className="whitespace-pre-wrap break-words rounded-md border bg-muted/50 p-3 text-xs">
                {JSON.stringify(initReq, null, 2)}
              </pre>
            </div>
            {initRes && (
              <div>
                <p className="mb-1 text-xs font-medium text-muted-foreground">
                  ← Server Response
                </p>
                <pre className="whitespace-pre-wrap break-words rounded-md border bg-green-50 p-3 text-xs text-green-900">
                  {JSON.stringify(initRes, null, 2)}
                </pre>
              </div>
            )}
          </div>
        )}

        {/* Step 2: tools/list */}
        {toolsReq && (
          <div className="space-y-3">
            <p className="text-xs font-semibold text-muted-foreground">
              Step 2 — tools/list
            </p>
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">
                → Client Request (POST /api/mcp)
              </p>
              <pre className="whitespace-pre-wrap break-words rounded-md border bg-muted/50 p-3 text-xs">
                {JSON.stringify(toolsReq, null, 2)}
              </pre>
            </div>
            {toolsRes && (
              <div>
                <p className="mb-1 text-xs font-medium text-muted-foreground">
                  ← Server Response ({(toolsRes as Record<string, unknown>).result
                    ? ((toolsRes as Record<string, unknown>).result as Record<string, unknown[]>).tools?.length ?? 0
                    : 0} tools)
                </p>
                <pre className="whitespace-pre-wrap break-words rounded-md border bg-green-50 p-3 text-xs text-green-900 max-h-64 overflow-y-auto">
                  {JSON.stringify(toolsRes, null, 2)}
                </pre>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── MCP Tool Playground ─────────────────────────────────────────────────────

async function callMcpTool(
  toolName: string,
  args: Record<string, unknown>,
): Promise<{ result: unknown; latency: number }> {
  const start = performance.now();
  const res = await fetch("/api/mcp", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: toolName, arguments: args },
    }),
  });
  const data = await res.json();
  return { result: data.result, latency: Math.round(performance.now() - start) };
}

function ToolPlayground() {
  const [query, setQuery] = useState("");
  const [pagePath, setPagePath] = useState("");
  const [inquiryEmail, setInquiryEmail] = useState("");
  const [inquiryMessage, setInquiryMessage] = useState("");
  const [results, setResults] = useState<ToolResult[]>([]);
  const [loading, setLoading] = useState(false);

  const runTool = useCallback(async (tool: string, args: Record<string, unknown>) => {
    setLoading(true);
    const { result, latency } = await callMcpTool(tool, args);
    setResults((prev) => [{ tool, args, response: result, latency }, ...prev]);
    setLoading(false);
  }, []);

  return (
    <div className="space-y-4">
      {/* search_site */}
      <div className="flex gap-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="search_site — enter a query..."
          onKeyDown={(e) => e.key === "Enter" && query.trim() && runTool("search_site", { query })}
        />
        <Button onClick={() => runTool("search_site", { query })} disabled={loading || !query.trim()}>
          <Search className="mr-2 h-4 w-4" />
          search_site
        </Button>
      </div>

      {/* get_page */}
      <div className="flex gap-2">
        <Input
          value={pagePath}
          onChange={(e) => setPagePath(e.target.value)}
          placeholder="get_page — enter a path (e.g. 'blog/my-post' or '' for homepage)..."
          onKeyDown={(e) => e.key === "Enter" && runTool("get_page", { path: pagePath })}
        />
        <Button onClick={() => runTool("get_page", { path: pagePath })} disabled={loading}>
          <FileText className="mr-2 h-4 w-4" />
          get_page
        </Button>
      </div>

      {/* Other tools */}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => runTool("list_pages", {})}
          disabled={loading}
        >
          <Globe className="mr-1 h-3 w-3" />
          list_pages
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => runTool("get_business_info", {})}
          disabled={loading}
        >
          <Bot className="mr-1 h-3 w-3" />
          get_business_info
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => runTool("list_services", {})}
          disabled={loading}
        >
          list_services
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => runTool("list_products", {})}
          disabled={loading}
        >
          list_products
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => runTool("get_reviews", {})}
          disabled={loading}
        >
          get_reviews
        </Button>
      </div>

      {/* submit_inquiry */}
      <div className="flex flex-col gap-2 rounded-md border p-3">
        <p className="text-xs font-medium text-muted-foreground">submit_inquiry — email &amp; message required</p>
        <div className="flex gap-2">
          <Input
            value={inquiryEmail}
            onChange={(e) => setInquiryEmail(e.target.value)}
            placeholder="Email address"
            className="flex-1"
          />
          <Input
            value={inquiryMessage}
            onChange={(e) => setInquiryMessage(e.target.value)}
            placeholder="Message"
            className="flex-[2]"
            onKeyDown={(e) =>
              e.key === "Enter" &&
              inquiryEmail.trim() &&
              inquiryMessage.trim() &&
              runTool("submit_inquiry", {
                name: "Demo User",
                email: inquiryEmail,
                message: inquiryMessage,
              })
            }
          />
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              runTool("submit_inquiry", {
                name: "Demo User",
                email: inquiryEmail,
                message: inquiryMessage,
              })
            }
            disabled={loading || !inquiryEmail.trim() || !inquiryMessage.trim()}
          >
            <Send className="mr-1 h-3 w-3" />
            submit_inquiry
          </Button>
        </div>
      </div>

      {results.length > 0 && (
        <div className="space-y-3">
          {results.map((r, i) => (
            <div key={i} className="rounded-md border bg-muted/50 p-3">
              <div className="mb-2 flex items-center justify-between">
                <code className="text-xs font-semibold text-primary">
                  tools/call → {r.tool}
                </code>
                <span className="text-xs text-muted-foreground">
                  {r.latency}ms
                </span>
              </div>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs text-foreground/80">
                {typeof r.response === "string"
                  ? r.response
                  : JSON.stringify(r.response, null, 2)}
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

export default function AiSurfacePage() {
  const [syncStats, setSyncStats] = useState<SyncStats | null>(null);
  const [syncedProject, setSyncedProject] = useState<Project | null>(null);
  const [synced, setSynced] = useState(false);
  const [connected, setConnected] = useState(false);

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

          {/* Endpoint status cards — always visible after sync */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-medium">
                  <Server className="h-4 w-4" />
                  MCP Endpoint
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex items-center justify-between">
                  <code className="text-xs text-muted-foreground">/api/mcp</code>
                  {connected ? (
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
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-medium">
                  <FileText className="h-4 w-4" />
                  llms.txt
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex items-center justify-between">
                  <code className="text-xs text-muted-foreground">/llms.txt</code>
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-green-100 px-2.5 py-0.5 text-xs font-medium text-green-800">
                    <span className="h-1.5 w-1.5 rounded-full bg-green-500" />
                    Live
                  </span>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-medium">
                  <Globe className="h-4 w-4" />
                  MCP Discovery
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex items-center justify-between">
                  <code className="text-xs text-muted-foreground">/.well-known/mcp</code>
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-green-100 px-2.5 py-0.5 text-xs font-medium text-green-800">
                    <span className="h-1.5 w-1.5 rounded-full bg-green-500" />
                    Live
                  </span>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-medium">
                  <FileJson className="h-4 w-4" />
                  ai-index.json
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex items-center justify-between">
                  <code className="text-xs text-muted-foreground">/ai-index.json</code>
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-green-100 px-2.5 py-0.5 text-xs font-medium text-green-800">
                    <span className="h-1.5 w-1.5 rounded-full bg-green-500" />
                    Live
                  </span>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Static content — available immediately after sync (no handshake needed) */}
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

          {/* MCP Handshake — gate for tool execution */}
          <McpHandshake
            connected={connected}
            onConnect={handleConnect}
            onDisconnect={handleDisconnect}
          />
        </>
      )}

      {/* After handshake — only Tool Playground needs a live session */}
      {connected && syncedProject && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Tool Playground</CardTitle>
            <CardDescription>
              Call any of the 8 MCP tools live. Each call is a real JSON-RPC
              POST to /api/mcp serving {syncedProject.name}&apos;s data.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ToolPlayground />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
