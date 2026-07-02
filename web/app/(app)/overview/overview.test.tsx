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
});
