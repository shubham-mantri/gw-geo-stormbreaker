"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronUp } from "lucide-react";

import { apiClient } from "@/lib/api";
import type { Role } from "@/lib/auth";
import { getToken } from "@/lib/auth";
import type { Prompt } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

/** Roles that may add/reorder prompts (backend: `POST /brands/{id}/prompts` requires `role >= editor`). */
const CAN_EDIT_ROLES: Role[] = ["owner", "admin", "editor"];

export type PromptManagerProps = {
  brandId: string;
  role: Role | null;
};

/**
 * Seed-prompt manager (ui-spec §3.8): list, add, and prioritize (reorder) the prompt set sampled
 * across engines. Backed by `GET/POST /brands/{id}/prompts`. The backend prompt API is
 * append-only (a singular `POST` create — no bulk-replace or reorder endpoint), so **adding**
 * persists via `savePrompts` (one create for the new prompt, merged into the list on success),
 * while **reordering** is a client-side view concern only — array order still doubles as priority
 * order, but is not persisted until a reorder endpoint lands (flagged in M2-T21's CONCERNS).
 */
export function PromptManager({ brandId, role }: PromptManagerProps) {
  const canEdit = role !== null && CAN_EDIT_ROLES.includes(role);
  const queryClient = useQueryClient();
  const [text, setText] = useState("");

  const promptsQuery = useQuery({
    queryKey: ["prompts", brandId],
    queryFn: () => apiClient(getToken).prompts(brandId),
  });

  const saveMutation = useMutation({
    mutationFn: (added: Prompt[]) => apiClient(getToken).savePrompts(brandId, added),
    onSuccess: (created) => {
      // Append the created rows (carrying their real backend ids) to the existing set.
      queryClient.setQueryData<Prompt[]>(["prompts", brandId], (old) => [
        ...(old ?? []),
        ...created,
      ]);
    },
  });

  const prompts = promptsQuery.data ?? [];

  function onAdd() {
    if (!canEdit || text.trim() === "") return;
    const newPrompt: Prompt = {
      id: `tmp-${Date.now()}`,
      text: text.trim(),
      intent_cluster: "",
      geo: "us",
      persona: "",
    };
    saveMutation.mutate([newPrompt]);
    setText("");
  }

  function move(index: number, direction: -1 | 1) {
    if (!canEdit) return;
    const target = index + direction;
    if (target < 0 || target >= prompts.length) return;
    const next = [...prompts];
    const [moved] = next.splice(index, 1);
    next.splice(target, 0, moved);
    // Reorder is client-side only — the backend prompt API is append-only (see the doc comment).
    queryClient.setQueryData<Prompt[]>(["prompts", brandId], next);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Seed prompts</CardTitle>
        <CardDescription>
          The prompts we sample across engines to measure your visibility. Reorder to prioritize.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {promptsQuery.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : prompts.length === 0 ? (
          <p className="text-sm text-muted-foreground">No prompts yet — add your first one below.</p>
        ) : (
          <ul className="divide-y rounded-md border">
            {prompts.map((p, i) => (
              <li key={p.id} className="flex items-center gap-3 px-3 py-2">
                <span className="flex-1 text-sm">{p.text}</span>
                <span className="text-xs text-muted-foreground">#{i + 1}</span>
                <div className="flex gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label={`Move ${p.text} up`}
                    disabled={!canEdit || i === 0}
                    onClick={() => move(i, -1)}
                  >
                    <ChevronUp className="h-4 w-4" />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label={`Move ${p.text} down`}
                    disabled={!canEdit || i === prompts.length - 1}
                    onClick={() => move(i, 1)}
                  >
                    <ChevronDown className="h-4 w-4" />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}

        <div className="flex items-end gap-2">
          <div className="flex-1 space-y-2">
            <Label htmlFor="prompt-manager-new">Add a prompt</Label>
            <Input
              id="prompt-manager-new"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder='e.g. "best CRM for startups"'
              disabled={!canEdit}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  onAdd();
                }
              }}
            />
          </div>
          <Button type="button" onClick={onAdd} disabled={!canEdit || text.trim() === ""}>
            Add
          </Button>
        </div>
        {!canEdit ? (
          <p className="text-xs text-muted-foreground">You need editor access to manage prompts.</p>
        ) : null}
      </CardContent>
    </Card>
  );
}
