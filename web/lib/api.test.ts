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
});
