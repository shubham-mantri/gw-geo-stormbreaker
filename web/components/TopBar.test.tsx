import { screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

import { TopBar } from "./TopBar";
import { setSession, clearSession } from "@/lib/auth";
import { renderWithClient, mockApi } from "@/test/utils";

// TopBar routes to /onboarding via next/navigation's useRouter. Mock it (hoisted per vitest rules)
// with spies we can assert on; that's the only next/navigation export TopBar uses.
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => router,
}));

describe("TopBar", () => {
  beforeEach(() => {
    clearSession();
    router.push.mockClear();
    router.replace.mockClear();
    setSession({ accessToken: "t", refreshToken: "r", role: "editor", tenantId: "t1" });
  });

  afterEach(() => {
    clearSession();
  });

  it("renders the brand switcher alongside an Add-brand control", async () => {
    mockApi();
    renderWithClient(<TopBar />);

    expect(await screen.findByLabelText("Brand")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /add brand/i }),
    ).toBeInTheDocument();
  });

  it("navigates to /onboarding when Add brand is clicked", async () => {
    mockApi();
    renderWithClient(<TopBar />);

    fireEvent.click(await screen.findByRole("button", { name: /add brand/i }));
    expect(router.push).toHaveBeenCalledWith("/onboarding");
  });
});
