import { screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";

import OverviewPage from "./page";
import { renderWithClient, mockApi } from "@/test/utils";

describe("OverviewPage", () => {
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
});
