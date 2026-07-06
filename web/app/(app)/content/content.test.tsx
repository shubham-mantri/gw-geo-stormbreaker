import { screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import ContentPage from "./page";
import { ApiError } from "@/lib/api";
import { setSession, clearSession } from "@/lib/auth";
import { renderWithClient, mockApi } from "@/test/utils";

// The Content screen reads the `?content_id` deep-link via next/navigation's useSearchParams.
// Mock it (hoisted, per vitest rules) with a controllable value; default = no param.
const search = vi.hoisted(() => ({ current: new URLSearchParams("") }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => search.current,
}));

const GENERATED = {
  content_id: "draft-42",
  draft: {
    id: "draft-42",
    tenant_id: "t1",
    brand_id: "b1",
    prompt_id: null,
    target_engine: null,
    intent_cluster: null,
    title: "Why Acme is the best CRM for startups",
    body_markdown: "Acme integrates with 200+ CRMs and ships in minutes.",
    schema_jsonld: {},
    grounded_fact_ids: [],
    status: "draft",
  },
  guardrails: { claims_ok: true, originality_ok: true },
};

describe("ContentPage", () => {
  beforeEach(() => {
    clearSession();
    search.current = new URLSearchParams("");
  });

  it("adds a knowledge-base fact and shows the added count", async () => {
    setSession({ accessToken: "t", refreshToken: "r", role: "editor", tenantId: "t1" });
    mockApi();
    renderWithClient(<ContentPage />);

    const factInput = await screen.findByLabelText(/^fact$/i);
    fireEvent.change(factInput, { target: { value: "Acme integrates with 200+ CRMs" } });
    fireEvent.click(screen.getByRole("button", { name: /add fact/i }));

    expect(await screen.findByText(/added 1 fact to the knowledge base/i)).toBeInTheDocument();
  });

  it("generates a draft and renders its body + guardrail badges", async () => {
    setSession({ accessToken: "t", refreshToken: "r", role: "editor", tenantId: "t1" });
    mockApi({ content: GENERATED });
    renderWithClient(<ContentPage />);

    fireEvent.change(await screen.findByLabelText(/^prompt$/i), {
      target: { value: "best CRM for startups" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^generate$/i }));

    expect(
      await screen.findByText(/Why Acme is the best CRM for startups/),
    ).toBeInTheDocument();
    expect(screen.getByText(/integrates with 200\+ CRMs/)).toBeInTheDocument();
    expect(screen.getByText(/claims verified/i)).toBeInTheDocument();
    expect(screen.getByText(/originality ok/i)).toBeInTheDocument();
  });

  it("flags a failing guardrail on the generated draft", async () => {
    setSession({ accessToken: "t", refreshToken: "r", role: "editor", tenantId: "t1" });
    mockApi({
      content: { ...GENERATED, guardrails: { claims_ok: false, originality_ok: true } },
    });
    renderWithClient(<ContentPage />);

    fireEvent.change(await screen.findByLabelText(/^prompt$/i), {
      target: { value: "x" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^generate$/i }));

    expect(await screen.findByText(/claims unverified/i)).toBeInTheDocument();
  });

  it("approves then publishes a generated draft", async () => {
    setSession({ accessToken: "t", refreshToken: "r", role: "editor", tenantId: "t1" });
    mockApi({ content: GENERATED });
    renderWithClient(<ContentPage />);

    fireEvent.change(await screen.findByLabelText(/^prompt$/i), {
      target: { value: "best CRM" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^generate$/i }));
    await screen.findByText(/Why Acme is the best CRM/);

    fireEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    expect(await screen.findByText(/status: approved/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^publish$/i }));
    const link = await screen.findByRole("link", { name: /hosted\.gwgeo\.io/i });
    expect(link).toHaveAttribute("href", "https://hosted.gwgeo.io/p/c1");
  });

  it("surfaces a 409 approval-gate rejection gracefully", async () => {
    setSession({ accessToken: "t", refreshToken: "r", role: "editor", tenantId: "t1" });
    const client = mockApi({ content: GENERATED });
    client.approveContent = () =>
      Promise.reject(new ApiError(409, "guardrails did not pass"));
    renderWithClient(<ContentPage />);

    fireEvent.change(await screen.findByLabelText(/^prompt$/i), {
      target: { value: "best CRM" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^generate$/i }));
    await screen.findByText(/Why Acme is the best CRM/);

    fireEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    expect(await screen.findByText(/approval blocked/i)).toBeInTheDocument();
  });

  it("opens the approve/publish gate for a draft deep-linked from an opportunity", async () => {
    setSession({ accessToken: "t", refreshToken: "r", role: "editor", tenantId: "t1" });
    search.current = new URLSearchParams("content_id=spawned-9");
    mockApi();
    renderWithClient(<ContentPage />);

    expect(
      await screen.findByText(/spawned from an opportunity/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^approve$/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^publish$/i })).toBeEnabled();
  });

  it("disables KB, approve and publish actions for a viewer", async () => {
    setSession({ accessToken: "t", refreshToken: "r", role: "viewer", tenantId: "t1" });
    mockApi({ content: GENERATED });
    renderWithClient(<ContentPage />);

    // KB add is gated.
    expect(await screen.findByLabelText(/^fact$/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: /add fact/i })).toBeDisabled();

    // Generate is open to any role; the approve/publish gate is not.
    fireEvent.change(screen.getByLabelText(/^prompt$/i), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: /^generate$/i }));
    await screen.findByText(/Why Acme is the best CRM/);

    expect(screen.getByRole("button", { name: /^approve$/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^publish$/i })).toBeDisabled();
  });
});
