import { screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import OverviewPage from "./page";
import { setSession, clearSession } from "@/lib/auth";
import { renderWithClient, mockApi } from "@/test/utils";

describe("OverviewPage", () => {
  beforeEach(() => {
    // Role gates the Run-measurement button; clear any prior session so each test controls it.
    clearSession();
  });

  it("renders KPI cards from overview data", async () => {
    mockApi({
      overview: {
        sov: 0.19,
        mention_rate: 0.38,
        pipeline: 480000,
        leads: 137,
        trend: [],
      },
    });
    renderWithClient(<OverviewPage />);

    expect(await screen.findByText("19%")).toBeInTheDocument(); // SoV
    expect(await screen.findByText("38%")).toBeInTheDocument(); // Mention rate
    expect(
      await screen.findByText(/\$480,?000|\$480k/),
    ).toBeInTheDocument(); // AI pipeline
    expect(await screen.findByText("137")).toBeInTheDocument(); // Leads
  });

  it("shows an onboarding empty state when no brand exists", async () => {
    mockApi({ brands: [] });
    renderWithClient(<OverviewPage />);

    const link = await screen.findByRole("link", { name: /onboarding|set up|get started/i });
    expect(link).toHaveAttribute("href", "/onboarding");
  });

  it("labels the SoV competitor series as all competitors combined, not a single name", async () => {
    // PRD §13: trend[].competitor is (1 − SoV) = ALL competitors combined, so the label must not
    // be pinned to competitors[0] ("Beta").
    mockApi({
      brands: [
        { id: "b1", name: "Acme", domain: "acme.com", competitors: ["Beta", "Gamma"] },
      ],
      overview: {
        sov: 0.2,
        mention_rate: 0.3,
        pipeline: 1000,
        leads: 5,
        // Empty trend keeps the SoV chart in its no-data state (recharts' ResponsiveContainer
        // needs a ResizeObserver, absent in jsdom); the card title we assert on renders regardless.
        trend: [],
      },
    });
    renderWithClient(<OverviewPage />);

    expect(
      await screen.findByText(/you vs\.? all competitors/i),
    ).toBeInTheDocument();
    // the single competitor's name is never used as the series/title label
    expect(screen.queryByText("Beta")).not.toBeInTheDocument();
  });

  it("runs a measurement and shows a non-blocking confirmation for an editor", async () => {
    setSession({ accessToken: "t", refreshToken: "r", role: "editor", tenantId: "t1" });
    const client = mockApi({
      overview: { sov: 0.1, mention_rate: 0.2, pipeline: 100, leads: 3, trend: [] },
      measure: {
        status: "accepted",
        brand_id: "b1",
        engines: ["perplexity", "openai"],
        n_samples: 8,
      },
    });
    const measureSpy = vi.spyOn(client, "measureBrand");
    renderWithClient(<OverviewPage />);

    const btn = await screen.findByRole("button", { name: /run measurement/i });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);

    // Calls the API for the active (auto-selected) brand …
    await waitFor(() => expect(measureSpy).toHaveBeenCalledWith("b1"));
    // … and surfaces a non-blocking confirmation echoing the scheduled engines.
    expect(
      await screen.findByText(/measurement started for perplexity, openai/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/refresh to see it/i)).toBeInTheDocument();
  });

  it("disables Run measurement for a viewer and explains why", async () => {
    setSession({ accessToken: "t", refreshToken: "r", role: "viewer", tenantId: "t1" });
    mockApi({
      overview: { sov: 0.1, mention_rate: 0.2, pipeline: 100, leads: 3, trend: [] },
    });
    renderWithClient(<OverviewPage />);

    expect(
      await screen.findByRole("button", { name: /run measurement/i }),
    ).toBeDisabled();
    expect(
      screen.getByText(/need editor access to run a measurement/i),
    ).toBeInTheDocument();
  });
});
