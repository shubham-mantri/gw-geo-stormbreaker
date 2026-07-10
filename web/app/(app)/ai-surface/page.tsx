"use client";

import { useCallback, useState } from "react";
import {
  Bot,
  CheckCircle2,
  FileText,
  Globe,
  Play,
  Search,
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

type ToolResult = {
  tool: string;
  args: Record<string, unknown>;
  response: unknown;
  latency: number;
};

function StatusBadge({ ok }: { ok: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${ok ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-green-500" : "bg-red-500"}`}
      />
      {ok ? "Live" : "Offline"}
    </span>
  );
}

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
  const [query, setQuery] = useState("jet aeration");
  const [results, setResults] = useState<ToolResult[]>([]);
  const [loading, setLoading] = useState(false);

  const runSearch = useCallback(async () => {
    setLoading(true);
    const { result, latency } = await callMcpTool("search_site", { query });
    setResults((prev) => [
      { tool: "search_site", args: { query }, response: result, latency },
      ...prev,
    ]);
    setLoading(false);
  }, [query]);

  const runTool = useCallback(async (tool: string, args: Record<string, unknown>) => {
    setLoading(true);
    const { result, latency } = await callMcpTool(tool, args);
    setResults((prev) => [{ tool, args, response: result, latency }, ...prev]);
    setLoading(false);
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search query..."
          onKeyDown={(e) => e.key === "Enter" && runSearch()}
        />
        <Button onClick={runSearch} disabled={loading}>
          <Search className="mr-2 h-4 w-4" />
          Search
        </Button>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => runTool("get_business_info", {})}
          disabled={loading}
        >
          <Bot className="mr-1 h-3 w-3" />
          Business Info
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => runTool("list_services", {})}
          disabled={loading}
        >
          Services
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => runTool("list_products", {})}
          disabled={loading}
        >
          Products
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => runTool("get_reviews", {})}
          disabled={loading}
        >
          Reviews
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => runTool("list_pages", {})}
          disabled={loading}
        >
          All Pages
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => runTool("get_page", { path: "" })}
          disabled={loading}
        >
          Homepage
        </Button>
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

function LlmsTxtViewer() {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [file, setFile] = useState<"llms.txt" | "llms-full.txt">("llms.txt");

  const loadFile = useCallback(
    async (f: "llms.txt" | "llms-full.txt") => {
      setFile(f);
      setLoading(true);
      const res = await fetch(`/${f}`);
      setContent(await res.text());
      setLoading(false);
    },
    [],
  );

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <Button
          variant={file === "llms.txt" ? "default" : "outline"}
          size="sm"
          onClick={() => loadFile("llms.txt")}
          disabled={loading}
        >
          <FileText className="mr-1 h-3 w-3" />
          llms.txt
        </Button>
        <Button
          variant={file === "llms-full.txt" ? "default" : "outline"}
          size="sm"
          onClick={() => loadFile("llms-full.txt")}
          disabled={loading}
        >
          <FileText className="mr-1 h-3 w-3" />
          llms-full.txt
        </Button>
      </div>
      {content !== null && (
        <pre className="max-h-96 overflow-auto rounded-md border bg-muted/50 p-4 text-xs leading-relaxed">
          {content}
        </pre>
      )}
    </div>
  );
}

function McpDiscovery() {
  const [discovery, setDiscovery] = useState<unknown>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    const res = await fetch("/.well-known/mcp");
    setDiscovery(await res.json());
    setLoaded(true);
  }, []);

  return (
    <div className="space-y-3">
      <Button variant="outline" size="sm" onClick={load}>
        <Globe className="mr-1 h-3 w-3" />
        Fetch /.well-known/mcp
      </Button>
      {loaded && (
        <pre className="rounded-md border bg-muted/50 p-4 text-xs">
          {JSON.stringify(discovery, null, 2)}
        </pre>
      )}
    </div>
  );
}

export default function AiSurfacePage() {
  const [initialized, setInitialized] = useState(false);
  const [initResult, setInitResult] = useState<unknown>(null);

  const handleInitialize = useCallback(async () => {
    const res = await fetch("/api/mcp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: 0,
        method: "initialize",
        params: { protocolVersion: "2025-06-18" },
      }),
    });
    const data = await res.json();
    setInitResult(data.result);
    setInitialized(true);
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">AI Surface</h1>
        <p className="text-sm text-muted-foreground">
          Open MCP server + llms.txt for AI discoverability — every client site
          gets an AI-queryable endpoint automatically.
        </p>
      </div>

      {/* Status row */}
      <div className="grid gap-4 sm:grid-cols-3">
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
              <StatusBadge ok />
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
              <StatusBadge ok />
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm font-medium">
              <Globe className="h-4 w-4" />
              Discovery
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center justify-between">
              <code className="text-xs text-muted-foreground">
                /.well-known/mcp
              </code>
              <StatusBadge ok />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Initialize */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">MCP Protocol Handshake</CardTitle>
          <CardDescription>
            Initialize a session with the MCP server (stateless — no persistent
            session).
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!initialized ? (
            <Button onClick={handleInitialize}>
              <Play className="mr-2 h-4 w-4" />
              Send initialize
            </Button>
          ) : (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-sm text-green-700">
                <CheckCircle2 className="h-4 w-4" />
                Connected — protocol {(initResult as Record<string, unknown>)?.protocolVersion as string}
              </div>
              <pre className="rounded-md border bg-muted/50 p-3 text-xs">
                {JSON.stringify(initResult, null, 2)}
              </pre>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Tool Playground */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Tool Playground</CardTitle>
          <CardDescription>
            Call any of the 8 MCP tools live. Each call is a real JSON-RPC POST
            to /api/mcp.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ToolPlayground />
        </CardContent>
      </Card>

      {/* llms.txt viewer */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">llms.txt Viewer</CardTitle>
          <CardDescription>
            The site's machine-readable overview, following the llms.txt
            specification. AI crawlers fetch this at the domain root.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <LlmsTxtViewer />
        </CardContent>
      </Card>

      {/* Discovery */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">MCP Discovery Document</CardTitle>
          <CardDescription>
            GET /.well-known/mcp — tells MCP clients where the server lives and
            how to connect.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <McpDiscovery />
        </CardContent>
      </Card>
    </div>
  );
}
