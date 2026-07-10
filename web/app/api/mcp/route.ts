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

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: CORS_HEADERS });
}

export async function POST(request: NextRequest) {
  const hostname = request.headers.get("host") || "localhost:3099";
  const rootDomain = hostname.replace(/:\d+$/, "");

  let body: JsonRpcRequest | JsonRpcRequest[];
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } },
      { status: 400, headers: CORS_HEADERS },
    );
  }

  const index = loadIndex();
  const rpcCtx: RpcContext = { index, rootDomain, hostname };

  if (Array.isArray(body)) {
    const results = (
      await Promise.all(body.map((m) => handleRpc(m, rpcCtx)))
    ).filter(Boolean);
    if (!results.length) {
      return new NextResponse(null, { status: 202, headers: CORS_HEADERS });
    }
    return NextResponse.json(results, { headers: CORS_HEADERS });
  }

  const result = await handleRpc(body, rpcCtx);
  if (!result) {
    return new NextResponse(null, { status: 202, headers: CORS_HEADERS });
  }
  return NextResponse.json(result, { headers: CORS_HEADERS });
}

export async function GET() {
  return NextResponse.json(
    { error: "Use POST for JSON-RPC. GET /.well-known/mcp for discovery." },
    { status: 405, headers: { Allow: "POST, OPTIONS", ...CORS_HEADERS } },
  );
}
