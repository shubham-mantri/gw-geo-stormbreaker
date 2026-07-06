import { screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import OpportunitiesPage from "./page";
import { setSession, clearSession } from "@/lib/auth";
import { renderWithClient, mockApi } from "@/test/utils";

const OPPS = [
  {
    id: "o-low",
    title: "Sentiment neutral on Gemini",
    rationale: "Add proof/data to lift sentiment.",
    est_impact: 0.12,
    engine: "gemini",
  },
  {
    id: "o-high",
    title: "You're absent for 'best CRM for startups'",
    rationale: "Beta ranks #1 via 6 Reddit threads + a G2 listicle.",
    est_impact: 0.42,
    engine: null,
  },
];

describe("OpportunitiesPage", () => {
  beforeEach(() => {
    clearSession();
  });

  it("lists opportunities ranked by est_impact with engine + % impact", async () => {
    setSession({ accessToken: "t", refreshToken: "r", role: "editor", tenantId: "t1" });
    mockApi({ opportunities: OPPS });
    renderWithClient(<OpportunitiesPage />);

    expect(
      await screen.findByText(/best CRM for startups/),
    ).toBeInTheDocument();
    // % impact + engine badges render
    expect(screen.getByText(/42% est\. impact/)).toBeInTheDocument();
    expect(screen.getByText(/12% est\. impact/)).toBeInTheDocument();
    expect(screen.getByText("gemini")).toBeInTheDocument();
    expect(screen.getByText("all engines")).toBeInTheDocument(); // engine === null

    // Ranked: the 0.42 row appears before the 0.12 row.
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent(/best CRM for startups/);
    expect(items[1]).toHaveTextContent(/Sentiment neutral on Gemini/);
  });

  it("refreshes and re-fetches the opportunity queue", async () => {
    setSession({ accessToken: "t", refreshToken: "r", role: "editor", tenantId: "t1" });
    const client = mockApi({ opportunities: OPPS });
    const refreshSpy = vi.spyOn(client, "refreshOpportunities");
    const listSpy = vi.spyOn(client, "opportunities");
    renderWithClient(<OpportunitiesPage />);

    await screen.findByText(/best CRM for startups/);
    fireEvent.click(screen.getByRole("button", { name: /^refresh$/i }));

    await waitFor(() => expect(refreshSpy).toHaveBeenCalledTimes(1));
    // invalidateQueries re-fetches the list (initial load + post-refresh refetch).
    await waitFor(() => expect(listSpy.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(await screen.findByText(/refresh queued/i)).toBeInTheDocument();
  });

  it("acts on an opportunity and links to the spawned draft in Content", async () => {
    setSession({ accessToken: "t", refreshToken: "r", role: "editor", tenantId: "t1" });
    mockApi({ opportunities: [OPPS[1]] });
    renderWithClient(<OpportunitiesPage />);

    await screen.findByText(/best CRM for startups/);
    fireEvent.click(screen.getByRole("button", { name: /fix this/i }));

    const reviewLink = await screen.findByRole("link", { name: /review draft/i });
    expect(reviewLink).toHaveAttribute(
      "href",
      "/content?content_id=spawned-content",
    );
  });

  it("disables refresh and act for a viewer (backend requires role >= editor)", async () => {
    setSession({ accessToken: "t", refreshToken: "r", role: "viewer", tenantId: "t1" });
    mockApi({ opportunities: [OPPS[1]] });
    renderWithClient(<OpportunitiesPage />);

    await screen.findByText(/best CRM for startups/);
    expect(screen.getByRole("button", { name: /^refresh$/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /fix this/i })).toBeDisabled();
  });
});
