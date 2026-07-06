"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, ShieldAlert } from "lucide-react";

import { ApiError, apiClient } from "@/lib/api";
import type { Role } from "@/lib/auth";
import { getRole, getToken } from "@/lib/auth";
import { useFilters } from "@/lib/filters";
import type {
  Brand,
  ContentDraft,
  GuardrailBadges,
  KbFactIn,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
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

/** Roles that may ingest facts / approve / publish (backend gates each on `role >= editor`). */
const CAN_EDIT_ROLES: Role[] = ["owner", "admin", "editor"];

/** A 409 from approve/publish is the *expected* human/guardrail gate, not a failure to hide. */
function gateMessage(err: unknown, blocked: string): string {
  if (err instanceof ApiError && err.status === 409) return blocked;
  return err instanceof Error ? err.message : "Something went wrong";
}

function ContentSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading content workspace">
      <Skeleton className="h-8 w-48" />
      {Array.from({ length: 2 }).map((_, i) => (
        <Card key={i}>
          <CardContent className="space-y-3 pt-6">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-10 w-full" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <Card className="mx-auto mt-10 max-w-lg text-center">
      <CardHeader>
        <CardTitle>No brand yet</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Set up a brand first — the content workspace grounds every draft in
          that brand&apos;s knowledge base.
        </p>
        <Button asChild>
          <Link href="/onboarding">Start onboarding</Link>
        </Button>
      </CardContent>
    </Card>
  );
}

function GuardrailBadge({
  ok,
  okLabel,
  failLabel,
}: {
  ok: boolean;
  okLabel: string;
  failLabel: string;
}) {
  return (
    <Badge variant={ok ? "success" : "destructive"}>
      {ok ? (
        <CheckCircle2 className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
      ) : (
        <ShieldAlert className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
      )}
      {ok ? okLabel : failLabel}
    </Badge>
  );
}

/** §3.5 "populate the KB so grounding has data" — `POST /brands/{id}/kb/facts` (role ≥ editor). */
function KbFactsCard({ brandId, canEdit }: { brandId: string; canEdit: boolean }) {
  const [text, setText] = useState("");
  const [category, setCategory] = useState("");

  const mutation = useMutation({
    mutationFn: (facts: KbFactIn[]) => apiClient(getToken).ingestKbFacts(brandId, facts),
    onSuccess: () => {
      setText("");
      setCategory("");
    },
  });

  function onAdd() {
    if (!canEdit || text.trim() === "") return;
    const fact: KbFactIn = { text: text.trim() };
    if (category.trim() !== "") fact.category = category.trim();
    mutation.mutate([fact]);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Knowledge base</CardTitle>
        <CardDescription>
          Approved facts the generator grounds and claim-checks against. Add a
          few before generating.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-[16rem] flex-1 space-y-2">
            <Label htmlFor="kb-fact-text">Fact</Label>
            <Input
              id="kb-fact-text"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="e.g. Acme integrates with 200+ CRMs"
              disabled={!canEdit}
            />
          </div>
          <div className="w-40 space-y-2">
            <Label htmlFor="kb-fact-category">Category (optional)</Label>
            <Input
              id="kb-fact-category"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              placeholder="e.g. integrations"
              disabled={!canEdit}
            />
          </div>
          <Button
            type="button"
            onClick={onAdd}
            disabled={!canEdit || text.trim() === "" || mutation.isPending}
            title={!canEdit ? "You need editor access to add facts" : undefined}
          >
            {mutation.isPending ? "Adding…" : "Add fact"}
          </Button>
        </div>
        {mutation.isSuccess ? (
          <p className="text-sm text-success">
            Added {mutation.data.added} fact{mutation.data.added === 1 ? "" : "s"} to
            the knowledge base.
          </p>
        ) : null}
        {mutation.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {mutation.error instanceof Error
              ? mutation.error.message
              : "Failed to add fact"}
          </p>
        ) : null}
        {!canEdit ? (
          <p className="text-xs text-muted-foreground">
            You need editor access to add knowledge-base facts.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ContentWorkspace() {
  const searchParams = useSearchParams();
  const { brandId } = useFilters();
  const role = getRole();
  const canEdit = role !== null && CAN_EDIT_ROLES.includes(role);

  const [prompt, setPrompt] = useState("");
  // The draft/guardrails the workspace is acting on. Seeded from `?content_id` when arriving from
  // an opportunity's "Fix this" (the draft body isn't fetchable — there's no GET — but the id is
  // enough to drive approve/publish); replaced wholesale by a fresh Generate.
  const [contentId, setContentId] = useState<string | null>(() =>
    searchParams.get("content_id"),
  );
  const [draft, setDraft] = useState<ContentDraft | null>(null);
  const [guardrails, setGuardrails] = useState<GuardrailBadges | null>(null);
  const spawnedFromOpportunity = contentId !== null && draft === null;

  const brandsQuery = useQuery({
    queryKey: ["brands"],
    queryFn: () => apiClient(getToken).brands(),
  });

  const activeBrandId = brandId ?? brandsQuery.data?.[0]?.id ?? null;
  const activeBrand: Brand | undefined = brandsQuery.data?.find(
    (b) => b.id === activeBrandId,
  );

  const approveMutation = useMutation({
    mutationFn: (id: string) => apiClient(getToken).approveContent(id),
  });
  const publishMutation = useMutation({
    mutationFn: (id: string) => apiClient(getToken).publishContent(id, "hosted"),
  });
  const generateMutation = useMutation({
    mutationFn: (promptText: string) =>
      apiClient(getToken).generateContent({
        brand_id: activeBrandId as string,
        prompt_text: promptText,
      }),
    onSuccess: (res) => {
      setContentId(res.content_id);
      setDraft(res.draft);
      setGuardrails(res.guardrails);
      approveMutation.reset();
      publishMutation.reset();
    },
  });

  if (brandsQuery.isLoading) return <ContentSkeleton />;

  if (!brandsQuery.data || brandsQuery.data.length === 0 || activeBrandId === null) {
    return <EmptyState />;
  }

  function onGenerate() {
    if (prompt.trim() === "") return;
    generateMutation.mutate(prompt.trim());
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Content</h1>
        <p className="text-sm text-muted-foreground">
          Draft grounded, guardrail-checked content for{" "}
          {activeBrand?.name ?? "your brand"}, then approve &amp; publish — the
          human gate.
        </p>
      </div>

      <KbFactsCard brandId={activeBrandId} canEdit={canEdit} />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Generate a draft</CardTitle>
          <CardDescription>
            Give the target search prompt; we draft an answer grounded in your
            knowledge base.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-[18rem] flex-1 space-y-2">
              <Label htmlFor="content-prompt">Prompt</Label>
              <Input
                id="content-prompt"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder='e.g. "best CRM for startups"'
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    onGenerate();
                  }
                }}
              />
            </div>
            <Button
              type="button"
              onClick={onGenerate}
              disabled={prompt.trim() === "" || generateMutation.isPending}
            >
              {generateMutation.isPending ? "Generating…" : "Generate"}
            </Button>
          </div>
          {generateMutation.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {generateMutation.error instanceof Error
                ? generateMutation.error.message
                : "Failed to generate a draft"}
            </p>
          ) : null}
        </CardContent>
      </Card>

      {draft ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{draft.title}</CardTitle>
            {guardrails ? (
              <div className="flex flex-wrap gap-2 pt-1">
                <GuardrailBadge
                  ok={guardrails.claims_ok}
                  okLabel="Claims verified"
                  failLabel="Claims unverified"
                />
                <GuardrailBadge
                  ok={guardrails.originality_ok}
                  okLabel="Originality OK"
                  failLabel="Originality flagged"
                />
              </div>
            ) : null}
          </CardHeader>
          <CardContent>
            <div className="whitespace-pre-wrap rounded-md border bg-muted/30 p-4 text-sm leading-relaxed">
              {draft.body_markdown}
            </div>
          </CardContent>
        </Card>
      ) : null}

      {contentId ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Approve &amp; publish</CardTitle>
            <CardDescription>
              The human gate — nothing publishes without an authorized approval
              and passing guardrails.
              {spawnedFromOpportunity
                ? " This draft was spawned from an opportunity."
                : null}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                variant="outline"
                disabled={!canEdit || approveMutation.isPending}
                onClick={() => approveMutation.mutate(contentId)}
                title={!canEdit ? "You need editor access to approve" : undefined}
              >
                {approveMutation.isPending ? "Approving…" : "Approve"}
              </Button>
              <Button
                type="button"
                disabled={!canEdit || publishMutation.isPending}
                onClick={() => publishMutation.mutate(contentId)}
                title={!canEdit ? "You need editor access to publish" : undefined}
              >
                {publishMutation.isPending ? "Publishing…" : "Publish"}
              </Button>
            </div>

            {approveMutation.isSuccess ? (
              <p className="text-sm text-success">
                Status: {approveMutation.data.status}
              </p>
            ) : null}
            {approveMutation.isError ? (
              <p role="alert" className="text-sm text-destructive">
                {gateMessage(
                  approveMutation.error,
                  "Approval blocked: the draft's guardrails didn't pass.",
                )}
              </p>
            ) : null}

            {publishMutation.isSuccess ? (
              <p className="text-sm text-success">
                Published ({publishMutation.data.status}) —{" "}
                <a
                  href={publishMutation.data.published_url}
                  target="_blank"
                  rel="noreferrer"
                  className="underline underline-offset-4"
                >
                  {publishMutation.data.published_url}
                </a>
              </p>
            ) : null}
            {publishMutation.isError ? (
              <p role="alert" className="text-sm text-destructive">
                {gateMessage(
                  publishMutation.error,
                  "Publish blocked: approve the draft first.",
                )}
              </p>
            ) : null}

            {!canEdit ? (
              <p className="text-xs text-muted-foreground">
                You need editor access to approve or publish.
              </p>
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

/**
 * Content workspace (ui-spec §3.5) — the execution surface. Populate the brand KB, generate a
 * grounded + guardrail-checked draft, then the explicit human gate (approve → publish). Wrapped in
 * Suspense because `ContentWorkspace` reads the `?content_id` deep-link (from an opportunity's "Fix
 * this") via `useSearchParams`, which Next requires under a Suspense boundary.
 */
export default function ContentPage() {
  return (
    <Suspense fallback={<ContentSkeleton />}>
      <ContentWorkspace />
    </Suspense>
  );
}
