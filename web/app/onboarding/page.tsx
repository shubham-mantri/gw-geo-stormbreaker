"use client";

import { useRouter } from "next/navigation";

import { OnboardingWizard } from "@/components/OnboardingWizard";

/**
 * First-run onboarding (ui-spec §4): brand -> competitors -> integrations -> seed prompts ->
 * "measuring…". A **top-level** route, deliberately outside `(app)/` — a brand-new tenant with zero
 * brands has nowhere to switch to in the top bar yet, and the `(app)` layout's auth-guard redirect
 * would otherwise fight with this page's own flow. `OnboardingWizard` itself has no router
 * dependency; this page supplies `onComplete` to send the user on to Overview once they're done
 * watching the "measuring" state.
 */
export default function OnboardingPage() {
  const router = useRouter();

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted/30 p-4">
      <OnboardingWizard onComplete={() => router.replace("/overview")} />
    </main>
  );
}
