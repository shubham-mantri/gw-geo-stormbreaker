import { NextRequest, NextResponse } from "next/server";

import { handleRpc, loadIndex } from "@/lib/mcp";
import type { JsonRpcRequest, RpcContext } from "@/lib/mcp/types";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers":
    "Content-Type, Accept, Authorization, Mcp-Session-Id, Mcp-Protocol-Version",
  "Access-Control-Max-Age": "86400",
};

function extractClient(request: NextRequest) {
  const ua = request.headers.get("user-agent") || "unknown";
  const sessionId = request.headers.get("mcp-session-id") || undefined;
  const ip =
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    request.headers.get("x-real-ip") ||
    "local";

  let clientName = "unknown";
  if (ua.includes("claude-code")) clientName = "Claude Code";
  else if (ua.includes("claude")) clientName = "Claude Desktop";
  else if (ua.includes("cursor")) clientName = "Cursor";
  else if (ua.includes("vscode") || ua.includes("Visual Studio Code")) clientName = "VS Code";
  else if (ua.includes("chatgpt") || ua.includes("openai")) clientName = "ChatGPT";
  else if (ua.includes("Mozilla") || ua.includes("Chrome") || ua.includes("Safari")) clientName = "Browser";

  return { ua, sessionId, ip, clientName };
}

function logMcp(
  method: string,
  detail: string,
  client: ReturnType<typeof extractClient>,
  durationMs?: number,
) {
  const ts = new Date().toISOString();
  const dur = durationMs !== undefined ? ` | ${durationMs}ms` : "";
  const session = client.sessionId ? ` | session:${client.sessionId}` : "";
  console.log(
    `[MCP] ${ts} | ${method} | ${detail} | client:${client.clientName} | ip:${client.ip}${session}${dur} | ua:${client.ua}`,
  );
}

function describeRpc(msg: JsonRpcRequest): { method: string; detail: string } {
  const method = msg.method;
  if (method === "initialize") {
    const ver = (msg.params as Record<string, unknown>)?.protocolVersion ?? "?";
    return { method, detail: `protocolVersion:${ver}` };
  }
  if (method === "tools/list") {
    return { method, detail: "enumerate tools" };
  }
  if (method === "tools/call") {
    const params = msg.params as Record<string, unknown> | undefined;
    const name = params?.name ?? "?";
    const args = params?.arguments
      ? JSON.stringify(params.arguments)
      : "{}";
    return { method: `tools/call → ${name}`, detail: `args:${args}` };
  }
  if (method === "ping") {
    return { method, detail: "keepalive" };
  }
  if (method.startsWith("notifications/")) {
    return { method, detail: "notification (no response)" };
  }
  return { method, detail: "" };
}

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: CORS_HEADERS });
}

export async function POST(request: NextRequest) {
  const hostname = request.headers.get("host") || "localhost:3099";
  const rootDomain = hostname.replace(/:\d+$/, "");
  const client = extractClient(request);

  let body: JsonRpcRequest | JsonRpcRequest[];
  try {
    body = await request.json();
  } catch {
    logMcp("PARSE_ERROR", "invalid JSON body", client);
    return NextResponse.json(
      { jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } },
      { status: 400, headers: CORS_HEADERS },
    );
  }

  const index = loadIndex();
  const rpcCtx: RpcContext = { index, rootDomain, hostname };

  if (Array.isArray(body)) {
    logMcp("BATCH", `${body.length} requests`, client);
    const start = performance.now();
    const results = (
      await Promise.all(
        body.map(async (m) => {
          const { method, detail } = describeRpc(m);
          const t0 = performance.now();
          const res = await handleRpc(m, rpcCtx);
          logMcp(method, detail, client, Math.round(performance.now() - t0));
          return res;
        }),
      )
    ).filter(Boolean);
    const totalMs = Math.round(performance.now() - start);
    logMcp("BATCH_DONE", `${results.length} responses`, client, totalMs);
    if (!results.length) {
      return new NextResponse(null, { status: 202, headers: CORS_HEADERS });
    }
    return NextResponse.json(results, { headers: CORS_HEADERS });
  }

  const { method, detail } = describeRpc(body);
  const start = performance.now();
  const result = await handleRpc(body, rpcCtx);
  const durationMs = Math.round(performance.now() - start);
  logMcp(method, detail, client, durationMs);

  if (!result) {
    return new NextResponse(null, { status: 202, headers: CORS_HEADERS });
  }
  return NextResponse.json(result, { headers: CORS_HEADERS });
}

export async function GET(request: NextRequest) {
  const client = extractClient(request);
  logMcp("GET", "rejected — must use POST", client);
  return NextResponse.json(
    { error: "Use POST for JSON-RPC. GET /.well-known/mcp for discovery." },
    { status: 405, headers: { Allow: "POST, OPTIONS", ...CORS_HEADERS } },
  );
}
