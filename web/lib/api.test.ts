import { describe, it, expect, vi } from "vitest";
import { apiClient } from "./api";
describe("apiClient", () => {
  it("sends bearer token and parses brands", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([{ id: "b1", name: "Acme", domain: "acme.com", competitors: [] }])));
    vi.stubGlobal("fetch", fetchMock);
    const api = apiClient(() => "tok123");
    const brands = await api.brands();
    expect(brands[0].id).toBe("b1");
    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string,string>;
    expect(headers.Authorization).toBe("Bearer tok123");
  });

  it("wraps connectIntegration config in the backend-required { config } envelope", async () => {
    // review fix #10: the backend's IntegrationConnect body is { config }, not the raw dict.
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "connected" })));
    vi.stubGlobal("fetch", fetchMock);
    const api = apiClient(() => "tok123");
    await api.connectIntegration("hubspot", { access_token_ref: "ssm://x" });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({
      config: { access_token_ref: "ssm://x" },
    });
  });
});
