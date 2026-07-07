import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

import { OnboardingWizard } from "./OnboardingWizard";
import { mockApi } from "@/test/utils";

// `OnboardingWizard` is a plain client-state machine (no TanStack Query / FiltersProvider
// dependency — see the component's doc comment), so these tests use a bare `render()`, not
// `renderWithClient`.

describe("OnboardingWizard", () => {
  it("advances through the 5 steps to measuring state", async () => {
    render(<OnboardingWizard />);
    expect(screen.getByText(/step 1 of 5/i)).toBeInTheDocument(); // brand
    fireEvent.change(screen.getByLabelText(/brand name/i), { target: { value: "Acme" } });
    fireEvent.change(screen.getByLabelText(/domain/i), { target: { value: "acme.com" } });
    fireEvent.click(screen.getByRole("button", { name: /next/i })); // -> competitors
    expect(screen.getByText(/step 2 of 5/i)).toBeInTheDocument();
  });

  it("disables Next on step 1 until both brand name and domain are filled", () => {
    render(<OnboardingWizard />);
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/brand name/i), { target: { value: "Acme" } });
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/domain/i), { target: { value: "acme.com" } });
    expect(screen.getByRole("button", { name: /next/i })).toBeEnabled();
  });

  it("starts a suggest job, advances the visible stage while polling, and seeds competitors on done", async () => {
    vi.useFakeTimers();
    try {
      // mockApi's built-in progression: running/profiling -> running/researching -> done(Acme,[Beta]).
      const client = mockApi();
      vi.spyOn(client, "startBrandSuggest"); // call-through, so we can assert it was started

      render(<OnboardingWizard />);
      fireEvent.change(screen.getByLabelText(/domain/i), { target: { value: "acme.com" } });
      fireEvent.click(screen.getByRole("button", { name: /look up/i }));

      // The job is started with the typed domain; the first stage shows immediately (pre-poll).
      expect(client.startBrandSuggest).toHaveBeenCalledWith("acme.com");
      expect(screen.getByRole("status")).toHaveTextContent(/fetching your site/i);

      // Flush the start promise so the poll interval registers.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      // Poll 1 (~1.5s) -> the visible stage advances to "profiling".
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      expect(screen.getByRole("status")).toHaveTextContent(
        /analyzing your brand and product categories/i,
      );

      // Poll 2 -> "researching".
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      expect(screen.getByRole("status")).toHaveTextContent(
        /researching competitors across the web/i,
      );

      // Poll 3 -> "done": brand name prefilled + competitors seeded, polling stopped.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      expect(screen.getByDisplayValue("Acme")).toBeInTheDocument();
      expect(screen.queryByRole("status")).not.toBeInTheDocument(); // lookup finished

      // Competitors seeded into step 2 — present and removable (editable).
      fireEvent.click(screen.getByRole("button", { name: /next/i }));
      expect(screen.getByText(/step 2 of 5/i)).toBeInTheDocument();
      expect(screen.getByText("Beta")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /remove beta/i }));
      expect(screen.queryByText("Beta")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("prefills recommended seed prompts into step 4 on done, and shows the new 'generating prompts' stage", async () => {
    vi.useFakeTimers();
    try {
      // mockApi's done result carries seed_prompts alongside competitors.
      mockApi();

      render(<OnboardingWizard />);
      fireEvent.change(screen.getByLabelText(/domain/i), { target: { value: "acme.com" } });
      fireEvent.click(screen.getByRole("button", { name: /look up/i }));

      // The stepper renders the new "Generating prompts" stage row while the lookup runs.
      expect(screen.getByText("Generating prompts")).toBeInTheDocument();

      // Drive the poll to completion (profiling -> researching -> done).
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0); // flush the start promise -> register the poll
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500); // -> done
      });

      // Brand name prefilled + polling stopped.
      expect(screen.getByDisplayValue("Acme")).toBeInTheDocument();
      expect(screen.queryByRole("status")).not.toBeInTheDocument();

      // Advance to step 4 (competitors -> integrations -> seed prompts).
      fireEvent.click(screen.getByRole("button", { name: /next/i })); // -> step 2
      fireEvent.click(screen.getByRole("button", { name: /next/i })); // -> step 3
      fireEvent.click(screen.getByRole("button", { name: /next/i })); // -> step 4
      expect(screen.getByText(/step 4 of 5/i)).toBeInTheDocument();

      // The recommended seed prompts are prefilled and fully editable (removable), like competitors.
      expect(screen.getByText("best CRM for startups")).toBeInTheDocument();
      expect(screen.getByText("how do I migrate to a new CRM")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /remove best CRM for startups/i }));
      expect(screen.queryByText("best CRM for startups")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("falls back to manual entry (no block, no error) when starting the lookup fails", async () => {
    const client = mockApi();
    vi.spyOn(client, "startBrandSuggest").mockRejectedValue(new Error("start boom"));

    render(<OnboardingWizard />);
    fireEvent.change(screen.getByLabelText(/domain/i), { target: { value: "acme.com" } });
    fireEvent.click(screen.getByRole("button", { name: /look up/i }));

    // Silent: the progress UI clears once the start fails; no error alert.
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
    expect(client.startBrandSuggest).toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    // Manual entry still works: the name field is empty; type it, then advance to competitors.
    expect(screen.getByLabelText(/brand name/i)).toHaveValue("");
    fireEvent.change(screen.getByLabelText(/brand name/i), { target: { value: "Manual Co" } });
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(screen.getByText(/step 2 of 5/i)).toBeInTheDocument();
  });

  it("falls back to manual entry silently when the suggest job ends in error", async () => {
    vi.useFakeTimers();
    try {
      const client = mockApi();
      vi.spyOn(client, "getBrandSuggestStatus").mockResolvedValue({
        status: "error",
        stage: "researching",
        label: "Researching competitors across the web",
        result: null,
        error: "pipeline boom",
      });

      render(<OnboardingWizard />);
      fireEvent.change(screen.getByLabelText(/domain/i), { target: { value: "acme.com" } });
      fireEvent.click(screen.getByRole("button", { name: /look up/i }));

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0); // register the poll interval
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500); // poll -> error -> silent fallback
      });

      // No error surfaced; the progress UI is gone and the name stays empty for manual entry.
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
      expect(screen.getByLabelText(/brand name/i)).toHaveValue("");
    } finally {
      vi.useRealTimers();
    }
  });

  it("supports going back to a previous step", () => {
    render(<OnboardingWizard />);
    fireEvent.change(screen.getByLabelText(/brand name/i), { target: { value: "Acme" } });
    fireEvent.change(screen.getByLabelText(/domain/i), { target: { value: "acme.com" } });
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(screen.getByText(/step 2 of 5/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    expect(screen.getByText(/step 1 of 5/i)).toBeInTheDocument();
  });

  it("runs the full flow (competitors, integrations, prompts) to the measuring state and calls onComplete", async () => {
    mockApi();
    const onComplete = vi.fn();
    render(<OnboardingWizard onComplete={onComplete} />);

    // Step 1 — brand.
    fireEvent.change(screen.getByLabelText(/brand name/i), { target: { value: "Acme" } });
    fireEvent.change(screen.getByLabelText(/domain/i), { target: { value: "acme.com" } });
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Step 2 — competitors.
    expect(screen.getByText(/step 2 of 5/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/add a competitor/i), { target: { value: "Beta" } });
    fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
    expect(screen.getByText("Beta")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Step 3 — integrations.
    expect(screen.getByText(/step 3 of 5/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /connect hubspot/i }));
    expect(await screen.findByRole("button", { name: /^connected$/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Step 4 — seed prompts.
    expect(screen.getByText(/step 4 of 5/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/add a prompt/i), {
      target: { value: "best CRM for startups" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^add$/i }));
    fireEvent.click(screen.getByRole("button", { name: /start measuring/i }));

    // Step 5 — measuring.
    expect(await screen.findByText(/step 5 of 5/i)).toBeInTheDocument();
    expect(screen.getByText(/check back/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /go to overview/i }));
    expect(onComplete).toHaveBeenCalledOnce();
  });

  it("shows an error and stays on step 4 if finishing setup fails", async () => {
    const client = mockApi();
    vi.spyOn(client, "createBrand").mockRejectedValue(new Error("boom"));

    render(<OnboardingWizard />);
    fireEvent.change(screen.getByLabelText(/brand name/i), { target: { value: "Acme" } });
    fireEvent.change(screen.getByLabelText(/domain/i), { target: { value: "acme.com" } });
    fireEvent.click(screen.getByRole("button", { name: /next/i })); // -> competitors
    fireEvent.click(screen.getByRole("button", { name: /next/i })); // -> integrations
    fireEvent.click(screen.getByRole("button", { name: /next/i })); // -> seed prompts
    fireEvent.click(screen.getByRole("button", { name: /start measuring/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/boom/i);
    expect(screen.getByText(/step 4 of 5/i)).toBeInTheDocument();
  });
});
