import { NextRequest, NextResponse } from "next/server";

import { getDiscoveryDocument, loadIndex } from "@/lib/mcp";

export async function GET(request: NextRequest) {
  const hostname = request.headers.get("host") || "localhost:3099";
  const index = loadIndex();
  return NextResponse.json(getDiscoveryDocument(hostname, index), {
    headers: { "Cache-Control": "public, max-age=3600" },
  });
}
