"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/lib/api";
import type { Role } from "@/lib/auth";
import { getToken } from "@/lib/auth";
import type { LlmModelConfig } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/** Roles that may read/change the model (backend: `GET`/`PUT /settings/llm-model` require `role >= admin`). */
const CAN_EDIT_ROLES: Role[] = ["owner", "admin"];

/** Sentinel select value that reveals a free-text input for a custom model slug. */
const CUSTOM = "__custom__";

/**
 * Preset model options per gateway (M5). `local_claude` uses Claude CLI aliases; `portkey`/`direct`
 * use native Anthropic slugs. A "Custom…" option always allows a free-text slug too, so a model not
 * in the preset list is still selectable.
 */
const MODEL_OPTIONS: Record<string, string[]> = {
  local_claude: ["sonnet", "opus", "haiku", "opus[1m]"],
  portkey: ["claude-haiku-4-5-20251001", "claude-sonnet-4-5", "claude-opus-4-8"],
  direct: ["claude-opus-4-8", "claude-sonnet-4-5", "claude-haiku-4-5-20251001"],
};

/** Preset options for a gateway, guaranteeing the currently-saved value is always selectable. */
function optionsFor(gateway: string, current: string): string[] {
  const base = MODEL_OPTIONS[gateway] ?? [];
  return base.includes(current) ? base : [current, ...base];
}

function LlmModelRow({ config, canEdit }: { config: LlmModelConfig; canEdit: boolean }) {
  const options = optionsFor(config.gateway, config.chat_model);
  const [selected, setSelected] = useState<string>(config.chat_model);
  const [custom, setCustom] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isCustom = selected === CUSTOM;
  const chatModel = isCustom ? custom.trim() : selected;

  async function onSave() {
    if (!canEdit || !chatModel) return;
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      await apiClient(getToken).setLlmModelConfig({
        gateway: config.gateway,
        chat_model: chatModel,
      });
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save model");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 rounded-md border p-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <p className="text-sm font-medium">{config.gateway}</p>
        <p className="text-xs text-muted-foreground">Gateway (env-driven, read-only)</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label={`Chat model for ${config.gateway}`}
          className="h-9 rounded-md border border-input bg-background px-2 text-sm disabled:cursor-not-allowed disabled:opacity-50"
          value={selected}
          disabled={!canEdit}
          onChange={(e) => {
            setSelected(e.target.value);
            setSaved(false);
          }}
        >
          {options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
          <option value={CUSTOM}>Custom…</option>
        </select>
        {isCustom ? (
          <input
            aria-label={`Custom chat model for ${config.gateway}`}
            className="h-9 rounded-md border border-input bg-background px-2 text-sm disabled:cursor-not-allowed disabled:opacity-50"
            placeholder="model slug"
            value={custom}
            disabled={!canEdit}
            onChange={(e) => {
              setCustom(e.target.value);
              setSaved(false);
            }}
          />
        ) : null}
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!canEdit || saving || !chatModel}
          onClick={onSave}
          title={!canEdit ? "You need admin access to change the model" : undefined}
        >
          {saving ? "Saving…" : saved ? "Saved" : "Save"}
        </Button>
      </div>
      {error ? (
        <p role="alert" className="w-full text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export type LlmModelPanelProps = {
  role: Role | null;
};

/**
 * LLM model (M5 model-selection): the content-chat model per gateway, DB-stored and selectable.
 * The active gateway is env-driven (`GEO_LLM_GATEWAY`) and shown read-only; only the model is a
 * dropdown. Reading and changing the model require `role >= admin` server-side — for a non-admin the
 * controls are gated with a hint (never hidden), and no fetch is made (the backend would 403).
 */
export function LlmModelPanel({ role }: LlmModelPanelProps) {
  const canEdit = role !== null && CAN_EDIT_ROLES.includes(role);
  const query = useQuery({
    queryKey: ["llm-model-config"],
    queryFn: () => apiClient(getToken).llmModelConfig(),
    enabled: canEdit,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">LLM model</CardTitle>
        <CardDescription>
          The chat model used to generate content, per gateway. The active gateway is env-driven (
          <code className="rounded bg-muted px-1 py-0.5 text-xs">GEO_LLM_GATEWAY</code>) and
          read-only here — only the model is selectable.
          {!canEdit ? " Changing the model requires admin access." : null}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {!canEdit ? (
          <p className="text-sm text-muted-foreground">
            You need admin access to view and change the content chat model.
          </p>
        ) : query.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : query.data && query.data.length > 0 ? (
          query.data.map((cfg) => (
            <LlmModelRow key={cfg.gateway} config={cfg} canEdit={canEdit} />
          ))
        ) : (
          <p className="text-sm text-muted-foreground">No models configured yet.</p>
        )}
      </CardContent>
    </Card>
  );
}
