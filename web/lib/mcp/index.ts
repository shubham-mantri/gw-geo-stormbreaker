import fs from "fs";
import path from "path";

import type { AiIndex } from "./types";

export { handleRpc, getDiscoveryDocument } from "./handler";
export type { AiIndex } from "./types";

let cached: AiIndex | null = null;

export function loadIndex(): AiIndex | null {
  if (cached) return cached;
  const filePath = path.join(process.cwd(), "public", "ai-index.json");
  if (!fs.existsSync(filePath)) return null;
  try {
    cached = JSON.parse(fs.readFileSync(filePath, "utf-8")) as AiIndex;
    return cached;
  } catch {
    return null;
  }
}
