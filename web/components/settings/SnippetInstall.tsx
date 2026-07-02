"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/lib/api";
import { getToken } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export type SnippetInstallProps = {
  /** Kept for parity with the other Settings sections / cache-keying; see note below. */
  brandId: string | null;
};

/** Copy `text` to the clipboard, falling back to a hidden-textarea `execCommand` where the async
 * Clipboard API isn't available (older browsers, some test environments). */
async function copyToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const el = document.createElement("textarea");
  el.value = text;
  el.style.position = "fixed";
  el.style.opacity = "0";
  document.body.appendChild(el);
  el.select();
  document.execCommand("copy");
  document.body.removeChild(el);
}

/**
 * Lead-capture install snippet (ui-spec §3.8): fetch + display the `<script>` tag from
 * `GET /lead-capture/snippet?brand_id=`, with a one-click copy button. `brandId` is passed to the
 * client (the backend requires the `brand_id` query param) and gates the query until a brand is
 * selected.
 */
export function SnippetInstall({ brandId }: SnippetInstallProps) {
  const [copied, setCopied] = useState(false);

  const snippetQuery = useQuery({
    queryKey: ["lead-capture-snippet", brandId],
    // Only runs when brandId !== null (enabled gate), so the non-null assertion is safe.
    queryFn: () => apiClient(getToken).leadCaptureSnippet(brandId as string),
    enabled: brandId !== null,
  });

  async function onCopy() {
    if (!snippetQuery.data) return;
    try {
      await copyToClipboard(snippetQuery.data.snippet);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Copy failed silently (e.g. no clipboard permission) — the snippet is still visible and
      // selectable by hand.
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Lead-capture install snippet</CardTitle>
        <CardDescription>
          Paste this snippet before <code>&lt;/body&gt;</code> on every page you want to track.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {snippetQuery.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : (
          <>
            <pre className="overflow-x-auto rounded-md border bg-muted p-3 text-xs">
              <code>{snippetQuery.data?.snippet ?? ""}</code>
            </pre>
            <Button type="button" variant="outline" size="sm" onClick={onCopy}>
              {copied ? "Copied!" : "Copy snippet"}
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}
