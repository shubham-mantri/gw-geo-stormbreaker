"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import type { PromptMetric } from "@/lib/types";
import { cn, formatPct } from "@/lib/utils";

/**
 * Prompt-level detail for the Visibility screen (ui-spec §3.2). Each prompt is
 * an expandable row; expanding it reveals the "view sampled answers" drawer with
 * the sample count and per-prompt metrics, so the sampling behind every number
 * stays visible.
 */

function PromptRow({ prompt }: { prompt: PromptMetric }) {
  const [open, setOpen] = useState(false);
  const panelId = `prompt-panel-${prompt.prompt_id}`;
  const Chevron = open ? ChevronDown : ChevronRight;

  return (
    <div className="border-b last:border-b-0">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left text-sm transition-colors hover:bg-muted/50"
      >
        <Chevron className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="flex-1 font-medium">{prompt.text}</span>
        <span className="tabular-nums text-muted-foreground">
          {formatPct(prompt.mention_rate)} mention
        </span>
        <span className="tabular-nums text-muted-foreground">
          pos{" "}
          {prompt.avg_position === null ? "—" : prompt.avg_position.toFixed(1)}
        </span>
        <span className="tabular-nums text-muted-foreground">
          n={prompt.n_samples}
        </span>
      </button>
      <div
        id={panelId}
        hidden={!open}
        className="bg-muted/30 px-11 py-3 text-sm text-muted-foreground"
      >
        <p className="font-medium text-foreground">
          Based on {prompt.n_samples} sampled answers
        </p>
        <dl className="mt-2 grid grid-cols-2 gap-x-8 gap-y-1 sm:grid-cols-3">
          <div>
            <dt className="text-xs uppercase tracking-wide">Mention rate</dt>
            <dd className="tabular-nums text-foreground">
              {formatPct(prompt.mention_rate)}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide">Avg position</dt>
            <dd className="tabular-nums text-foreground">
              {prompt.avg_position === null
                ? "—"
                : prompt.avg_position.toFixed(1)}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide">Sampled answers</dt>
            <dd className="tabular-nums text-foreground">{prompt.n_samples}</dd>
          </div>
        </dl>
      </div>
    </div>
  );
}

export type PromptDrawerProps = {
  prompts: PromptMetric[];
  className?: string;
};

export function PromptDrawer({ prompts, className }: PromptDrawerProps) {
  if (!prompts || prompts.length === 0) {
    return (
      <p className={cn("px-4 py-6 text-sm text-muted-foreground", className)}>
        No prompt-level data yet.
      </p>
    );
  }

  return (
    <div className={cn("divide-border", className)}>
      {prompts.map((prompt) => (
        <PromptRow key={prompt.prompt_id} prompt={prompt} />
      ))}
    </div>
  );
}
