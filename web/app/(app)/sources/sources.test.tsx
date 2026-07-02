import { screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";

import SourcesPage from "./page";
import { renderWithClient, mockApi } from "@/test/utils";

describe("SourcesPage", () => {
  it("flags competitor gaps", async () => {
    mockApi({
      sources: [
        {
          domain: "reddit.com",
          source_type: "reddit",
          you_pct: 0.48,
          competitor_pcts: { Beta: 0.71 },
        },
      ],
    });
    renderWithClient(<SourcesPage />);

    expect(await screen.findByText("reddit.com")).toBeInTheDocument();
    expect(await screen.findByText(/gap/i)).toBeInTheDocument();
  });

  it("does not flag a source where you lead", async () => {
    mockApi({
      sources: [
        {
          domain: "acme.com",
          source_type: "own_site",
          you_pct: 0.61,
          competitor_pcts: { Beta: 0.04 },
        },
      ],
    });
    renderWithClient(<SourcesPage />);

    expect(await screen.findByText("acme.com")).toBeInTheDocument();
    expect(screen.queryByText(/gap/i)).not.toBeInTheDocument();
  });

  it("renders multiple competitor columns and flags each gap independently", async () => {
    mockApi({
      sources: [
        {
          domain: "g2.com",
          source_type: "review_site",
          you_pct: 0.32,
          competitor_pcts: { Beta: 0.55, Gamma: 0.1 },
        },
      ],
    });
    renderWithClient(<SourcesPage />);

    expect(await screen.findByText("g2.com")).toBeInTheDocument();
    expect(await screen.findByText(/cites.*beta/i)).toBeInTheDocument();
    expect(await screen.findByText(/cites.*gamma/i)).toBeInTheDocument();
    // Only the Beta column (55% > 32%) is a gap; Gamma (10%) is not.
    expect(await screen.findAllByText(/gap/i)).toHaveLength(1);
  });

  it("shows an onboarding empty state when no brand exists", async () => {
    mockApi({ brands: [] });
    renderWithClient(<SourcesPage />);

    const link = await screen.findByRole("link", {
      name: /onboarding|set up|get started/i,
    });
    expect(link).toHaveAttribute("href", "/onboarding");
  });
});
