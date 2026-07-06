import { screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import SettingsPage from "./page";
import { IntegrationsPanel } from "@/components/settings/IntegrationsPanel";
import { LlmModelPanel } from "@/components/settings/LlmModelPanel";
import { PromptManager } from "@/components/settings/PromptManager";
import { SnippetInstall } from "@/components/settings/SnippetInstall";
import { setSession, clearSession } from "@/lib/auth";
import { renderWithClient, mockApi } from "@/test/utils";

describe("SettingsPage", () => {
  beforeEach(() => {
    clearSession();
  });

  it("renders the brand summary, prompts, integrations, and install snippet", async () => {
    setSession({ accessToken: "t", refreshToken: "r", role: "owner", tenantId: "t1" });
    mockApi({
      prompts: [
        { id: "p1", text: "best CRM for startups", intent_cluster: "comparison", geo: "us", persona: "" },
      ],
      snippet: { snippet: '<script src="https://cdn.gwgeo.io/gwgeo.js" data-key="abc"></script>' },
    });
    renderWithClient(<SettingsPage />);

    expect(await screen.findByText("Acme")).toBeInTheDocument();
    expect(await screen.findByText(/best CRM for startups/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /connect hubspot/i })).toBeInTheDocument();
    expect(await screen.findByText(/data-key="abc"/)).toBeInTheDocument();
    expect(screen.getByText(/team & roles/i)).toBeInTheDocument();
    expect(screen.getByText(/single sign-on/i)).toBeInTheDocument();
  });

  it("shows an onboarding empty state when no brand exists", async () => {
    setSession({ accessToken: "t", refreshToken: "r", role: "owner", tenantId: "t1" });
    mockApi({ brands: [] });
    renderWithClient(<SettingsPage />);
    const link = await screen.findByRole("link", { name: /onboarding|set up|get started/i });
    expect(link).toHaveAttribute("href", "/onboarding");
  });

  it("marks the current session's role with a 'You' badge", async () => {
    setSession({ accessToken: "t", refreshToken: "r", role: "editor", tenantId: "t1" });
    mockApi();
    renderWithClient(<SettingsPage />);

    const editorRow = (await screen.findByText("Editor")).closest("tr");
    expect(editorRow).not.toBeNull();
    expect(within(editorRow as HTMLElement).getByText("You")).toBeInTheDocument();
  });
});

describe("PromptManager", () => {
  it("lists existing prompts and adds a new one when the role can edit", async () => {
    mockApi({
      prompts: [{ id: "p1", text: "best CRM for startups", intent_cluster: "", geo: "us", persona: "" }],
    });
    renderWithClient(<PromptManager brandId="b1" role="editor" />);

    expect(await screen.findByText(/best CRM for startups/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/add a prompt/i), {
      target: { value: "hubspot alternatives" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^add$/i }));

    expect(await screen.findByText(/hubspot alternatives/i)).toBeInTheDocument();
  });

  it("disables adding and reordering prompts for a viewer", async () => {
    mockApi({
      prompts: [{ id: "p1", text: "best CRM for startups", intent_cluster: "", geo: "us", persona: "" }],
    });
    renderWithClient(<PromptManager brandId="b1" role="viewer" />);

    expect(await screen.findByText(/best CRM for startups/)).toBeInTheDocument();
    expect(screen.getByLabelText(/add a prompt/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: /^add$/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /move best CRM for startups up/i })).toBeDisabled();
  });
});

describe("IntegrationsPanel", () => {
  it("shows connect buttons for hubspot/salesforce/ga4", () => {
    renderWithClient(<IntegrationsPanel role="admin" />);

    expect(screen.getByRole("button", { name: /connect hubspot/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /connect salesforce/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /connect ga4/i })).toBeEnabled();
  });

  it("disables connect buttons for a viewer (role gate: role >= admin)", () => {
    renderWithClient(<IntegrationsPanel role="viewer" />);

    expect(screen.getByRole("button", { name: /connect hubspot/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /connect salesforce/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /connect ga4/i })).toBeDisabled();
  });

  it("also disables connect buttons for an editor (backend requires role >= admin, not just non-viewer)", () => {
    renderWithClient(<IntegrationsPanel role="editor" />);

    expect(screen.getByRole("button", { name: /connect hubspot/i })).toBeDisabled();
  });

  it("connects an integration when clicked with sufficient role", async () => {
    mockApi();
    renderWithClient(<IntegrationsPanel role="owner" />);

    fireEvent.click(screen.getByRole("button", { name: /connect hubspot/i }));

    expect(await screen.findByText(/status: connected/i)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^connected$/i })).toBeDisabled(),
    );
  });
});

describe("LlmModelPanel", () => {
  it("renders the current model per gateway as a dropdown value (admin)", async () => {
    mockApi({
      llmModel: [
        { gateway: "local_claude", chat_model: "sonnet" },
        { gateway: "portkey", chat_model: "claude-haiku-4-5-20251001" },
      ],
    });
    renderWithClient(<LlmModelPanel role="admin" />);

    const localSelect = await screen.findByLabelText("Chat model for local_claude");
    expect(localSelect).toHaveValue("sonnet");
    const portkeySelect = screen.getByLabelText("Chat model for portkey");
    expect(portkeySelect).toHaveValue("claude-haiku-4-5-20251001");
    // The gateway itself is shown read-only (env-driven), not editable — one label per row.
    expect(screen.getAllByText(/gateway \(env-driven, read-only\)/i)).toHaveLength(2);
  });

  it("saves a picked model via PUT /settings/llm-model", async () => {
    const client = mockApi({ llmModel: [{ gateway: "local_claude", chat_model: "sonnet" }] });
    const saveSpy = vi.spyOn(client, "setLlmModelConfig");
    renderWithClient(<LlmModelPanel role="admin" />);

    const select = await screen.findByLabelText("Chat model for local_claude");
    fireEvent.change(select, { target: { value: "opus" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(saveSpy).toHaveBeenCalledWith({ gateway: "local_claude", chat_model: "opus" }),
    );
    expect(await screen.findByRole("button", { name: /saved/i })).toBeInTheDocument();
  });

  it("saves a free-text custom model", async () => {
    const client = mockApi({
      llmModel: [{ gateway: "portkey", chat_model: "claude-haiku-4-5-20251001" }],
    });
    const saveSpy = vi.spyOn(client, "setLlmModelConfig");
    renderWithClient(<LlmModelPanel role="admin" />);

    const select = await screen.findByLabelText("Chat model for portkey");
    fireEvent.change(select, { target: { value: "__custom__" } });
    fireEvent.change(screen.getByLabelText("Custom chat model for portkey"), {
      target: { value: "claude-opus-4-8" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(saveSpy).toHaveBeenCalledWith({ gateway: "portkey", chat_model: "claude-opus-4-8" }),
    );
  });

  it("gates the control for a non-admin (hint, no dropdown)", () => {
    mockApi();
    renderWithClient(<LlmModelPanel role="viewer" />);

    expect(screen.getByText(/admin access to view and change/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/chat model for/i)).not.toBeInTheDocument();
  });
});

describe("SnippetInstall", () => {
  it("renders the fetched snippet and a copy button", async () => {
    mockApi({ snippet: { snippet: '<script data-key="xyz"></script>' } });
    renderWithClient(<SnippetInstall brandId="b1" />);

    expect(await screen.findByText(/data-key="xyz"/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /copy/i })).toBeInTheDocument();
  });

  it("copies the snippet to the clipboard when the copy button is clicked", async () => {
    mockApi({ snippet: { snippet: '<script data-key="xyz"></script>' } });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    renderWithClient(<SnippetInstall brandId="b1" />);
    await screen.findByText(/data-key="xyz"/);

    fireEvent.click(screen.getByRole("button", { name: /copy/i }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith('<script data-key="xyz"></script>'));
    expect(await screen.findByRole("button", { name: /copied/i })).toBeInTheDocument();
  });
});
