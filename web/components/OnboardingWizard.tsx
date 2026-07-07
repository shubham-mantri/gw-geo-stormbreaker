"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Circle, Loader2, X } from "lucide-react";

import { apiClient } from "@/lib/api";
import { getToken } from "@/lib/auth";
import type { BrandSuggestStatus, IntegrationKind, Prompt } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const TOTAL_STEPS = 5;
const STEP_TITLES = ["Your brand", "Competitors", "Integrations", "Seed prompts", "Measuring"];

const AVAILABLE_INTEGRATIONS: { kind: IntegrationKind; label: string }[] = [
  { kind: "hubspot", label: "HubSpot" },
  { kind: "salesforce", label: "Salesforce" },
  { kind: "ga4", label: "GA4" },
];

/**
 * The ordered domain-lookup stages shown as a live stepper in step 1. Keys mirror the backend
 * `stage` values (`gw_geo/onboarding/suggest.py`); the terminal `done`/`error` stages aren't steps
 * (they end the poll). The short labels here are the stepper rows; the backend's fuller `label`
 * drives the aria-live status line so screen readers announce progress as it changes.
 */
const SUGGEST_STAGES: { key: string; label: string }[] = [
  { key: "fetching", label: "Fetching your site" },
  { key: "profiling", label: "Analyzing your brand" },
  { key: "researching", label: "Researching competitors" },
  { key: "refining", label: "Checking category coverage" },
];

/** Poll cadence for the async suggest job (matches the ui-spec's "~1-2 min" grounded lookup). */
const SUGGEST_POLL_MS = 1500;

/** Index of the currently-active step for a backend `stage` (`done` -> all complete; unknown/
 * `"starting"` -> the first step). Drives the stepper's active/complete/pending styling. */
function suggestActiveIndex(stage: string): number {
  if (stage === "done") return SUGGEST_STAGES.length;
  const i = SUGGEST_STAGES.findIndex((s) => s.key === stage);
  return i === -1 ? 0 : i;
}

export type OnboardingWizardProps = {
  /** Called once the user is done reading the "measuring" state and wants to continue. */
  onComplete?: () => void;
};

/**
 * First-run onboarding wizard (ui-spec §4): brand -> competitors -> integrations -> seed prompts ->
 * "measuring… check back". A plain client-state machine — deliberately **no** TanStack Query /
 * `useFilters` / `useRouter` dependency, so it renders standalone (see `OnboardingWizard.test.tsx`,
 * which mounts it with a bare `render()`, no providers). The host page wires `onComplete` to
 * navigation (see `app/onboarding/page.tsx`).
 *
 * Writes land only once, when step 4 finishes: `POST /brands` then (if any seed prompts were added)
 * `POST /brands/{id}/prompts` — so an abandoned wizard never half-creates a brand. Integrations are
 * tenant-scoped, not brand-scoped (`POST /integrations/{kind}` takes no brand id), so each connects
 * immediately when clicked in step 3, independent of brand creation.
 */
export function OnboardingWizard({ onComplete }: OnboardingWizardProps) {
  const [step, setStep] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Step 1 — brand. `domain` is the primary input: "Look up" starts the async lookup that auto-fills
  // `brandName` and seeds the competitors list from the site + LLM (both stay fully editable).
  const [brandName, setBrandName] = useState("");
  const [domain, setDomain] = useState("");
  const [lookingUp, setLookingUp] = useState(false);
  // Live progress of the async suggest job (backend `stage` key + human `label`), shown as a stepper
  // + an aria-live status line while `lookingUp`.
  const [progressStage, setProgressStage] = useState("");
  const [progressLabel, setProgressLabel] = useState("");
  // The poll interval handle, so we can clear it on done/error/unmount.
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function stopPolling() {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  // Clear any in-flight poll on unmount (an abandoned lookup must not keep polling).
  useEffect(() => stopPolling, []);

  // Step 2 — competitors.
  const [competitorInput, setCompetitorInput] = useState("");
  const [competitors, setCompetitors] = useState<string[]>([]);

  // Step 3 — integrations (tenant-scoped; connect immediately, independent of brand creation).
  const [connected, setConnected] = useState<Set<string>>(new Set());
  const [connectingKind, setConnectingKind] = useState<string | null>(null);

  // Step 4 — seed prompts.
  const [promptInput, setPromptInput] = useState("");
  const [prompts, setPrompts] = useState<string[]>([]);

  function goBack() {
    setError(null);
    setStep((s) => Math.max(1, s - 1));
  }

  /**
   * Domain-first auto-fill: **start** the async backend lookup, then poll it every ~1.5s for live
   * stage progress, and on completion pre-fill the brand name + seed the competitors (still
   * editable). The grounded pipeline takes ~1-2 min, so it runs as a job the client polls rather
   * than a single long request that the dev proxy would reset.
   *
   * Best-effort — a failed start, a failed poll, or a job that ends in `error` all fall back
   * silently to manual entry (no error shown); a lookup never blocks onboarding.
   */
  async function lookUp() {
    const value = domain.trim();
    if (value === "" || lookingUp) return;
    setLookingUp(true);
    setError(null);
    setProgressStage("fetching");
    setProgressLabel("Fetching your site");

    const client = apiClient(getToken);
    let job: { job_id: string };
    try {
      job = await client.startBrandSuggest(value);
    } catch {
      setLookingUp(false); // couldn't even start — fall back to manual entry
      return;
    }

    stopPolling();
    pollRef.current = setInterval(async () => {
      let status: BrandSuggestStatus;
      try {
        status = await client.getBrandSuggestStatus(job.job_id);
      } catch {
        stopPolling();
        setLookingUp(false); // poll failed (e.g. unknown/expired job) — fall back silently
        return;
      }
      setProgressStage(status.stage);
      setProgressLabel(status.label);
      if (status.status === "done") {
        stopPolling();
        if (status.result) {
          if (status.result.name) setBrandName(status.result.name);
          if (Array.isArray(status.result.competitors)) setCompetitors(status.result.competitors);
        }
        setLookingUp(false);
      } else if (status.status === "error") {
        stopPolling();
        setLookingUp(false); // job errored — silent fallback to manual entry (as today)
      }
    }, SUGGEST_POLL_MS);
  }

  function addCompetitor() {
    const value = competitorInput.trim();
    if (value === "") return;
    setCompetitors((c) => [...c, value]);
    setCompetitorInput("");
  }

  function removeCompetitor(name: string) {
    setCompetitors((c) => c.filter((x) => x !== name));
  }

  async function connectIntegration(kind: IntegrationKind) {
    setConnectingKind(kind as string);
    setError(null);
    try {
      await apiClient(getToken).connectIntegration(kind, {});
      setConnected((prev) => new Set(prev).add(kind as string));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to connect integration");
    } finally {
      setConnectingKind(null);
    }
  }

  function addPrompt() {
    const value = promptInput.trim();
    if (value === "") return;
    setPrompts((p) => [...p, value]);
    setPromptInput("");
  }

  function removePrompt(text: string) {
    setPrompts((p) => p.filter((x) => x !== text));
  }

  /** Step 4 -> 5: persist the brand + any seed prompts, then show the "measuring" state. */
  async function finishSetup() {
    setSubmitting(true);
    setError(null);
    try {
      const created = await apiClient(getToken).createBrand({
        name: brandName.trim(),
        domain: domain.trim(),
        competitors,
      });

      if (prompts.length > 0) {
        const rows: Prompt[] = prompts.map((text, i) => ({
          id: `seed-${i}`,
          text,
          intent_cluster: "",
          geo: "us",
          persona: "",
        }));
        await apiClient(getToken).savePrompts(created.id, rows);
      }

      setStep(5);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to finish setup");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="w-full max-w-lg">
      <CardHeader className="space-y-3">
        <p className="text-sm font-medium text-muted-foreground">
          Step {step} of {TOTAL_STEPS}
        </p>
        <CardTitle className="text-xl">{STEP_TITLES[step - 1]}</CardTitle>
        {step === 1 ? (
          <CardDescription>
            Start with your domain — we&apos;ll look up your brand name and competitors for you.
          </CardDescription>
        ) : null}
        {step === 2 ? <CardDescription>Who should we compare you against?</CardDescription> : null}
        {step === 3 ? (
          <CardDescription>
            Connect a CRM or GA4 to enrich pipeline attribution — optional, you can do this later in
            Settings.
          </CardDescription>
        ) : null}
        {step === 4 ? (
          <CardDescription>Add a few prompts you want AI engines measured on.</CardDescription>
        ) : null}
      </CardHeader>

      <CardContent className="space-y-4">
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}

        {step === 1 ? (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="onboarding-domain">Domain</Label>
              <div className="flex items-end gap-2">
                <Input
                  id="onboarding-domain"
                  className="flex-1"
                  value={domain}
                  onChange={(e) => setDomain(e.target.value)}
                  placeholder="acme.com"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      lookUp();
                    }
                  }}
                />
                <Button
                  type="button"
                  variant="outline"
                  onClick={lookUp}
                  disabled={domain.trim() === "" || lookingUp}
                >
                  {lookingUp ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
                      Looking up…
                    </>
                  ) : (
                    "Look up"
                  )}
                </Button>
              </div>
              {lookingUp ? (
                <div className="space-y-2 rounded-md border bg-muted/30 p-3">
                  <ol className="space-y-1.5">
                    {SUGGEST_STAGES.map((s, i) => {
                      const activeIndex = suggestActiveIndex(progressStage);
                      const isDone = i < activeIndex;
                      const isActive = i === activeIndex;
                      return (
                        <li key={s.key} className="flex items-center gap-2 text-sm">
                          {isDone ? (
                            <Check className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
                          ) : isActive ? (
                            <Loader2
                              className="h-3.5 w-3.5 shrink-0 animate-spin text-primary"
                              aria-hidden="true"
                            />
                          ) : (
                            <Circle
                              className="h-3.5 w-3.5 shrink-0 text-muted-foreground/40"
                              aria-hidden="true"
                            />
                          )}
                          <span
                            className={
                              isActive
                                ? "font-medium text-foreground"
                                : isDone
                                  ? "text-muted-foreground"
                                  : "text-muted-foreground/60"
                            }
                          >
                            {s.label}
                          </span>
                        </li>
                      );
                    })}
                  </ol>
                  <p role="status" aria-live="polite" className="text-xs text-muted-foreground">
                    {progressLabel || "Starting…"} — this can take a minute or two.
                  </p>
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Enter your domain and we&apos;ll pre-fill your brand name and competitors — all
                  editable.
                </p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="onboarding-brand-name">Brand name</Label>
              <Input
                id="onboarding-brand-name"
                value={brandName}
                onChange={(e) => setBrandName(e.target.value)}
                placeholder="Acme"
              />
            </div>
          </div>
        ) : null}

        {step === 2 ? (
          <div className="space-y-4">
            <div className="flex items-end gap-2">
              <div className="flex-1 space-y-2">
                <Label htmlFor="onboarding-competitor">Add a competitor</Label>
                <Input
                  id="onboarding-competitor"
                  value={competitorInput}
                  onChange={(e) => setCompetitorInput(e.target.value)}
                  placeholder="Beta"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addCompetitor();
                    }
                  }}
                />
              </div>
              <Button
                type="button"
                variant="outline"
                onClick={addCompetitor}
                disabled={competitorInput.trim() === ""}
              >
                Add
              </Button>
            </div>
            {competitors.length > 0 ? (
              <ul className="space-y-1">
                {competitors.map((name) => (
                  <li
                    key={name}
                    className="flex items-center justify-between rounded-md border px-3 py-1.5 text-sm"
                  >
                    {name}
                    <button
                      type="button"
                      aria-label={`Remove ${name}`}
                      onClick={() => removeCompetitor(name)}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">
                Optional — you can add competitors later in Settings.
              </p>
            )}
          </div>
        ) : null}

        {step === 3 ? (
          <div className="space-y-3">
            {AVAILABLE_INTEGRATIONS.map(({ kind, label }) => {
              const key = kind as string;
              const isConnected = connected.has(key);
              const isConnecting = connectingKind === key;
              return (
                <div
                  key={key}
                  className="flex items-center justify-between rounded-md border p-3"
                >
                  <span className="text-sm font-medium">{label}</span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={isConnected || isConnecting}
                    onClick={() => connectIntegration(kind)}
                  >
                    {isConnected ? "Connected" : isConnecting ? "Connecting…" : `Connect ${label}`}
                  </Button>
                </div>
              );
            })}
            <p className="text-sm text-muted-foreground">
              Optional — connect now or later from Settings.
            </p>
          </div>
        ) : null}

        {step === 4 ? (
          <div className="space-y-4">
            <div className="flex items-end gap-2">
              <div className="flex-1 space-y-2">
                <Label htmlFor="onboarding-prompt">Add a prompt</Label>
                <Input
                  id="onboarding-prompt"
                  value={promptInput}
                  onChange={(e) => setPromptInput(e.target.value)}
                  placeholder="best CRM for startups"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addPrompt();
                    }
                  }}
                />
              </div>
              <Button
                type="button"
                variant="outline"
                onClick={addPrompt}
                disabled={promptInput.trim() === ""}
              >
                Add
              </Button>
            </div>
            {prompts.length > 0 ? (
              <ul className="space-y-1">
                {prompts.map((text) => (
                  <li
                    key={text}
                    className="flex items-center justify-between rounded-md border px-3 py-1.5 text-sm"
                  >
                    {text}
                    <button
                      type="button"
                      aria-label={`Remove ${text}`}
                      onClick={() => removePrompt(text)}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">
                Add at least one prompt to start measuring, or skip and add later in Settings.
              </p>
            )}
          </div>
        ) : null}

        {step === 5 ? (
          <div className="space-y-2 py-6 text-center">
            <Loader2
              className="mx-auto h-8 w-8 animate-spin text-muted-foreground"
              aria-hidden="true"
            />
            <p className="font-medium">Measuring… check back soon</p>
            <p className="text-sm text-muted-foreground">
              We&apos;re sampling AI engines for {brandName || "your brand"}&apos;s first
              visibility snapshot. This can take a few minutes.
            </p>
          </div>
        ) : null}
      </CardContent>

      <CardFooter className="flex justify-between">
        {step > 1 && step < 5 ? (
          <Button type="button" variant="ghost" onClick={goBack}>
            Back
          </Button>
        ) : (
          <span />
        )}

        {step === 1 ? (
          <Button
            type="button"
            onClick={() => setStep(2)}
            disabled={brandName.trim() === "" || domain.trim() === ""}
          >
            Next
          </Button>
        ) : null}
        {step === 2 ? (
          <Button type="button" onClick={() => setStep(3)}>
            Next
          </Button>
        ) : null}
        {step === 3 ? (
          <Button type="button" onClick={() => setStep(4)}>
            Next
          </Button>
        ) : null}
        {step === 4 ? (
          <Button type="button" onClick={finishSetup} disabled={submitting}>
            {submitting ? "Starting…" : "Start measuring"}
          </Button>
        ) : null}
        {step === 5 ? (
          <Button type="button" className="ml-auto" onClick={() => onComplete?.()}>
            Go to Overview
          </Button>
        ) : null}
      </CardFooter>
    </Card>
  );
}
