import type { AiIndex, AiIndexPage } from "./types";

export type SearchResult = {
  page: AiIndexPage;
  score: number;
  snippet: string;
};

export function searchIndex(
  index: AiIndex,
  query: string,
  limit = 5,
): SearchResult[] {
  const tokens = (query.toLowerCase().match(/[a-z0-9]+/g) || []).filter(
    (t) => t.length > 1,
  );
  if (!tokens.length) return [];

  const phrase = query.toLowerCase().trim();
  const scored: SearchResult[] = [];

  for (const page of index.pages) {
    const title = (page.title || "").toLowerCase();
    const description = (page.description || "").toLowerCase();
    const body = (page.markdown || "").toLowerCase();
    let score = 0;

    for (const t of tokens) {
      if (title.includes(t)) score += 5;
      if (description.includes(t)) score += 3;
      let hits = 0;
      let i = body.indexOf(t);
      while (i !== -1 && hits < 5) {
        hits++;
        i = body.indexOf(t, i + t.length);
      }
      score += hits;
    }

    if (title.includes(phrase)) score += 8;
    else if (body.includes(phrase)) score += 4;

    if (score > 0) {
      scored.push({
        page,
        score,
        snippet: makeSnippet(page.markdown || "", phrase, tokens),
      });
    }
  }

  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, limit);
}

function makeSnippet(markdown: string, phrase: string, tokens: string[]): string {
  const lower = markdown.toLowerCase();
  let pos = lower.indexOf(phrase);
  if (pos === -1) {
    for (const t of tokens) {
      pos = lower.indexOf(t);
      if (pos !== -1) break;
    }
  }
  if (pos === -1) pos = 0;
  const start = Math.max(0, pos - 120);
  const end = Math.min(markdown.length, pos + 200);
  return (
    (start > 0 ? "…" : "") +
    markdown.slice(start, end).replace(/\s+/g, " ").trim() +
    (end < markdown.length ? "…" : "")
  );
}
