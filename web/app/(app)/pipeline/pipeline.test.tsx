import { fireEvent, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";

import PipelinePage from "./page";
import { renderWithClient, mockApi } from "@/test/utils";

const FULL_PIPELINE = {
  influenced: 480000,
  attributed: 92000,
  leads: 137,
  lift: 0.23,
  top_answers: [
    { prompt: "best CRM for SaaS startups", leads: 41, value: 210000 },
    { prompt: "HubSpot alternatives", leads: 28, value: 88000 },
  ],
  method_breakdown: {
    direct: 40000,
    citation_linked: 52000,
    assisted: 300000,
    holdout_incremental: 88000,
  },
  confidence_note:
    "Holdout incrementality is the only causal figure; others are correlational.",
};

describe("PipelinePage", () => {
  it("renders method breakdown and confidence note", async () => {
    mockApi({ pipeline: FULL_PIPELINE });
    renderWithClient(<PipelinePage />);

    expect(await screen.findByText(/holdout/i)).toBeInTheDocument(); // breakdown key
    expect(await screen.findByText(/only causal/i)).toBeInTheDocument(); // confidence note shown
    expect(await screen.findByText(/\$92,?000/)).toBeInTheDocument(); // attributed
  });

  it("renders headline KPI cards and top-converting answers", async () => {
    mockApi({ pipeline: FULL_PIPELINE });
    renderWithClient(<PipelinePage />);

    expect(await screen.findByText(/\$480,?000/)).toBeInTheDocument(); // influenced
    expect(await screen.findByText("137")).toBeInTheDocument(); // leads
    expect(await screen.findByText(/\+23%/)).toBeInTheDocument(); // incremental lift
    expect(
      await screen.findByText(/best CRM for SaaS startups/i),
    ).toBeInTheDocument();
    expect(await screen.findByText(/\$210,?000/)).toBeInTheDocument();
  });

  it("always renders all four attribution methods, even when every value is zero", async () => {
    mockApi({
      pipeline: {
        influenced: 0,
        attributed: 0,
        leads: 0,
        lift: 0,
        top_answers: [],
        method_breakdown: {
          direct: 0,
          citation_linked: 0,
          assisted: 0,
          holdout_incremental: 0,
        },
        confidence_note: "No AI-driven sessions in this range yet.",
      },
    });
    renderWithClient(<PipelinePage />);

    expect(await screen.findByText(/direct referral/i)).toBeInTheDocument();
    expect(await screen.findByText(/citation-linked/i)).toBeInTheDocument();
    expect(await screen.findByText(/^assisted$/i)).toBeInTheDocument();
    expect(
      await screen.findByText(/incremental \(causal\)/i),
    ).toBeInTheDocument();
    // The disclosure is never hidden, even when the pipeline is empty.
    expect(await screen.findByText(/how this is measured/i)).toBeInTheDocument();
    expect(
      await screen.findByText(/no ai-driven sessions/i),
    ).toBeInTheDocument();
  });

  it("never shows the attributed number without an export/method-mix control alongside it", async () => {
    mockApi({ pipeline: FULL_PIPELINE });
    renderWithClient(<PipelinePage />);

    // The bare number and its method mix land on the same screen render.
    expect(await screen.findByText(/\$92,?000/)).toBeInTheDocument();
    expect(await screen.findByText(/attribution method breakdown/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /export csv/i }),
    ).toBeInTheDocument();
  });

  it("exports CSV client-side without crashing (dependency-free)", async () => {
    mockApi({ pipeline: FULL_PIPELINE });
    renderWithClient(<PipelinePage />);

    const button = await screen.findByRole("button", { name: /export csv/i });
    expect(() => fireEvent.click(button)).not.toThrow();
  });

  it("shows an onboarding empty state when no brand exists", async () => {
    mockApi({ brands: [] });
    renderWithClient(<PipelinePage />);

    const link = await screen.findByRole("link", {
      name: /onboarding|set up|get started/i,
    });
    expect(link).toHaveAttribute("href", "/onboarding");
  });
});
