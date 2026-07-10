import { NextRequest, NextResponse } from "next/server";

import { getDiscoveryDocument } from "@/lib/mcp";

export async function GET(request: NextRequest) {
  const hostname = request.headers.get("host") || "localhost:3099";
  return NextResponse.json(getDiscoveryDocument(hostname), {
    headers: { "Cache-Control": "public, max-age=3600" },
  });
}
