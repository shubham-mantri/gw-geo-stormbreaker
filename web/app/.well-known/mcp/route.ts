import { NextRequest, NextResponse } from "next/server";

import { getDiscoveryDocument, loadIndex } from "@/lib/mcp";

export async function GET(request: NextRequest) {
  const hostname = request.headers.get("host") || "localhost:3099";
  const ua = request.headers.get("user-agent") || "unknown";
  const ip =
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    request.headers.get("x-real-ip") ||
    "local";

  console.log(
    `[MCP Discovery] ${new Date().toISOString()} | GET /.well-known/mcp | ip:${ip} | ua:${ua}`,
  );

  const index = loadIndex();
  return NextResponse.json(getDiscoveryDocument(hostname, index), {
    headers: { "Cache-Control": "public, max-age=3600" },
  });
}
