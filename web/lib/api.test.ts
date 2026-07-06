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

  it("POSTs opportunity act to the top-level /opportunities/{id}/act path", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ content_id: "c9" })));
    vi.stubGlobal("fetch", fetchMock);
    const api = apiClient(() => "tok123");
    const res = await api.actOnOpportunity("op 1");
    expect(res.content_id).toBe("c9");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/opportunities/op%201/act");
    expect(init.method).toBe("POST");
  });

  it("sends the connector envelope when publishing content", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "published", published_url: "https://h/1" })));
    vi.stubGlobal("fetch", fetchMock);
    const api = apiClient(() => "tok123");
    await api.publishContent("c1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/content/c1/publish");
    expect(JSON.parse(init.body as string)).toEqual({ connector: "hosted" });
  });

  it("throws ApiError with the status for a 409 approval-gate rejection", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("conflict", { status: 409 }));
    vi.stubGlobal("fetch", fetchMock);
    const api = apiClient(() => "tok123");
    await expect(api.approveContent("c1")).rejects.toMatchObject({ status: 409 });
  });
});
