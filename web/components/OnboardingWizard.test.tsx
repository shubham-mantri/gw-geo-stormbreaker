import { render, screen, fireEvent, waitFor } from "@testing-library/react";
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

  it("looks up the domain to prefill brand name and seed competitors (both editable)", async () => {
    const client = mockApi();
    vi.spyOn(client, "suggestBrand").mockResolvedValue({
      name: "Acme Corp",
      domain: "acme.com",
      competitors: ["Beta", "Gamma"],
    });

    render(<OnboardingWizard />);
    fireEvent.change(screen.getByLabelText(/domain/i), { target: { value: "acme.com" } });
    fireEvent.click(screen.getByRole("button", { name: /look up/i }));

    // Brand name prefilled into the editable field; the lookup used the typed domain.
    expect(await screen.findByDisplayValue("Acme Corp")).toBeInTheDocument();
    expect(client.suggestBrand).toHaveBeenCalledWith("acme.com");

    // Competitors seeded into step 2 — present and removable (editable).
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(screen.getByText(/step 2 of 5/i)).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.getByText("Gamma")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /remove beta/i }));
    expect(screen.queryByText("Beta")).not.toBeInTheDocument();
  });

  it("falls back to manual entry (no block, no error) when the lookup fails", async () => {
    const client = mockApi();
    vi.spyOn(client, "suggestBrand").mockRejectedValue(new Error("lookup boom"));

    render(<OnboardingWizard />);
    fireEvent.change(screen.getByLabelText(/domain/i), { target: { value: "acme.com" } });
    fireEvent.click(screen.getByRole("button", { name: /look up/i }));

    await waitFor(() => expect(client.suggestBrand).toHaveBeenCalled());
    // Silent: no error alert, and the name field stays empty for manual entry.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    // Manual entry still works: type the name, then advance to competitors.
    fireEvent.change(screen.getByLabelText(/brand name/i), { target: { value: "Manual Co" } });
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(screen.getByText(/step 2 of 5/i)).toBeInTheDocument();
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
