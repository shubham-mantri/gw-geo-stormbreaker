import { NextRequest, NextResponse } from "next/server";
import fs from "fs";
import path from "path";

import { buildAiIndex } from "@/lib/mcp/build-index";

const CONTENT_API_URL = "https://api.gushwork.ai/seo-v2";
const CONTENT_API_TOKEN = "UYfMYH826f3hQkWOnTWFjrCrzxD9Joe4C3O4NTWc";

export async function POST(request: NextRequest) {
  const { projectId } = (await request.json()) as { projectId: string };

  if (!projectId || typeof projectId !== "string") {
    return NextResponse.json(
      { error: "projectId is required" },
      { status: 400 },
    );
  }

  const url = `${CONTENT_API_URL}/project/${projectId}/content?process_group_id=`;
  const res = await fetch(url, {
    headers: { "x-api-key": CONTENT_API_TOKEN },
  });

  if (!res.ok) {
    return NextResponse.json(
      { error: `Content API returned ${res.status}` },
      { status: 502 },
    );
  }

  const payload = (await res.json()) as {
    files: { path: string; content: string }[];
  };

  if (!payload.files?.length) {
    return NextResponse.json(
      { error: "No files returned from content API" },
      { status: 404 },
    );
  }

  const hasProjectJson = payload.files.some((f) => f.path === "project.json");
  if (!hasProjectJson) {
    return NextResponse.json(
      { error: "This project doesn't have full content data (project.json missing)" },
      { status: 422 },
    );
  }

  const contentDir = path.join(process.cwd(), "synced-content");
  if (fs.existsSync(contentDir)) {
    fs.rmSync(contentDir, { recursive: true, force: true });
  }
  fs.mkdirSync(contentDir, { recursive: true });

  for (const file of payload.files) {
    const fullPath = path.join(contentDir, file.path);
    fs.mkdirSync(path.dirname(fullPath), { recursive: true });
    const output = file.path.endsWith(".json")
      ? JSON.stringify(JSON.parse(file.content), null, 2)
      : file.content;
    fs.writeFileSync(fullPath, output, "utf-8");
  }

  const index = buildAiIndex(contentDir);

  const publicDir = path.join(process.cwd(), "public");
  fs.writeFileSync(
    path.join(publicDir, "ai-index.json"),
    JSON.stringify(index, null, 2),
  );
  fs.writeFileSync(path.join(publicDir, "llms.txt"), index._llmsTxt);
  fs.writeFileSync(path.join(publicDir, "llms-full.txt"), index._llmsFullTxt);

  // Clear the in-memory MCP cache so next request picks up fresh data
  const { invalidateCache } = await import("@/lib/mcp");
  invalidateCache();

  return NextResponse.json({
    success: true,
    stats: {
      files: payload.files.length,
      pages: index.pages.length,
      products: index.products.length,
      services: index.services.length,
      reviews: index.reviews.length,
    },
  });
}
