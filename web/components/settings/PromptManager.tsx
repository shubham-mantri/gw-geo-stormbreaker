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
 * across engines. Backed by `GET/POST /brands/{id}/prompts`; `savePrompts` takes the *whole* next
 * list (per `lib/api.ts`'s `ApiClient` contract), so add/reorder both persist by sending the full,
 * updated array — array order doubles as priority order.
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
    mutationFn: (next: Prompt[]) => apiClient(getToken).savePrompts(brandId, next),
    onSuccess: (saved) => {
      queryClient.setQueryData(["prompts", brandId], saved);
    },
  });

  const prompts = promptsQuery.data ?? [];

  function onAdd() {
    if (!canEdit || text.trim() === "") return;
    const next: Prompt[] = [
      ...prompts,
      { id: `tmp-${Date.now()}`, text: text.trim(), intent_cluster: "", geo: "us", persona: "" },
    ];
    saveMutation.mutate(next);
    setText("");
  }

  function move(index: number, direction: -1 | 1) {
    if (!canEdit) return;
    const target = index + direction;
    if (target < 0 || target >= prompts.length) return;
    const next = [...prompts];
    const [moved] = next.splice(index, 1);
    next.splice(target, 0, moved);
    saveMutation.mutate(next);
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
