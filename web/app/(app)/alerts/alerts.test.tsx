import { screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";

import AlertsPage from "./page";
import { renderWithClient, mockApi } from "@/test/utils";

describe("AlertsPage", () => {
  it("colours alerts by severity", async () => {
    mockApi({
      alerts: [{ severity: "red", message: "ChatGPT visibility -8%", ts: "2026-06-30T00:00:00Z" }],
    });
    renderWithClient(<AlertsPage />);
    const item = await screen.findByText(/ChatGPT visibility/);
    expect(item.closest("[data-severity='red']")).not.toBeNull();
  });

  it("renders green and yellow severities with distinct markers", async () => {
    mockApi({
      alerts: [
        { severity: "green", message: 'Now #1 recommendation for "CRM for startups"', ts: "2026-06-29T00:00:00Z" },
        { severity: "yellow", message: 'New competitor "Gamma" appearing in 4 prompts', ts: "2026-06-28T00:00:00Z" },
      ],
    });
    renderWithClient(<AlertsPage />);

    const win = await screen.findByText(/Now #1 recommendation/);
    expect(win.closest("[data-severity='green']")).not.toBeNull();

    const heads = await screen.findByText(/New competitor "Gamma"/);
    expect(heads.closest("[data-severity='yellow']")).not.toBeNull();
  });

  it("shows an empty state message when there are no alerts", async () => {
    mockApi({ alerts: [] });
    renderWithClient(<AlertsPage />);
    expect(await screen.findByText(/no alerts yet/i)).toBeInTheDocument();
  });

  it("shows an onboarding empty state when no brand exists", async () => {
    mockApi({ brands: [] });
    renderWithClient(<AlertsPage />);
    const link = await screen.findByRole("link", { name: /onboarding|set up|get started/i });
    expect(link).toHaveAttribute("href", "/onboarding");
  });
});
