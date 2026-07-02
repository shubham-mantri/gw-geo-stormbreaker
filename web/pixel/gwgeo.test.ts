import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const COLLECT_URL = "https://api.gwgeo.io/lead-capture/collect";

function mockFetchOk(): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 202 }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function installScriptTag(attrs: Record<string, string>): HTMLScriptElement {
  const script = document.createElement("script");
  for (const [key, value] of Object.entries(attrs)) script.setAttribute(key, value);
  document.body.appendChild(script);
  // `document.currentScript` is a getter-only property; shadow it with an own property the way a
  // real browser would set it while this <script> is executing.
  Object.defineProperty(document, "currentScript", { value: script, configurable: true });
  return script;
}

function setReferrer(url: string): void {
  Object.defineProperty(document, "referrer", { value: url, configurable: true });
}

beforeEach(() => {
  // Clean slate for the first-party visitor id and page context before every test.
  document.cookie = "gwgeo_vid=; Max-Age=0; Path=/;";
  localStorage.clear();
  Object.defineProperty(document, "currentScript", { value: null, configurable: true });
  setReferrer("");
  window.history.replaceState({}, "", "/");
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.innerHTML = "";
  window.gwgeo = undefined;
});

describe("parseUtm", () => {
  it("extracts utm_* params, lower-cased, and ignores non-utm params", async () => {
    const { parseUtm } = await import("./gwgeo");
    expect(parseUtm("?utm_source=chatgpt&UTM_Medium=referral&ref=xyz")).toEqual({
      utm_source: "chatgpt",
      utm_medium: "referral",
    });
  });

  it("returns {} for an empty query string", async () => {
    const { parseUtm } = await import("./gwgeo");
    expect(parseUtm("")).toEqual({});
  });
});

describe("buildBeacon (pure)", () => {
  it("builds a session beacon carrying the write-key, visitor id, referrer, and utm", async () => {
    const { buildBeacon } = await import("./gwgeo");
    const beacon = buildBeacon("gwk_123", {
      href: "https://acme.com/crm?utm_source=chatgpt",
      referrer: "https://chatgpt.com/",
      search: "?utm_source=chatgpt",
      userAgent: "test-agent",
      visitorId: "v-abc",
    });
    expect(beacon).toEqual({
      write_key: "gwk_123",
      type: "session",
      visitor_id: "v-abc",
      landing_url: "https://acme.com/crm?utm_source=chatgpt",
      referrer: "https://chatgpt.com/",
      utm: { utm_source: "chatgpt" },
      user_agent: "test-agent",
      ts: expect.any(String),
    });
  });

  it("omits referrer/user_agent (rather than sending empty strings) when absent", async () => {
    const { buildBeacon } = await import("./gwgeo");
    const beacon = buildBeacon("gwk_123", { href: "https://acme.com/", search: "", visitorId: "v1" });
    expect(beacon.referrer).toBeUndefined();
    expect(beacon.user_agent).toBeUndefined();
    expect(beacon.utm).toEqual({});
  });
});

describe("buildLeadBeacon (pure)", () => {
  it("maps the public payload (value/crmStage/selfReportedSource) onto LeadEvent field names", async () => {
    const { buildLeadBeacon } = await import("./gwgeo");
    const beacon = buildLeadBeacon("gwk_123", "v-abc", {
      email: "a@x.com",
      value: 1000,
      crmStage: "closed_won",
      selfReportedSource: "chatgpt",
    });
    expect(beacon).toEqual({
      write_key: "gwk_123",
      type: "lead",
      visitor_id: "v-abc",
      email: "a@x.com",
      value_usd: 1000,
      crm_stage: "closed_won",
      self_reported_source: "chatgpt",
      ts: expect.any(String),
    });
  });

  it("omits fields the caller didn't pass", async () => {
    const { buildLeadBeacon } = await import("./gwgeo");
    const beacon = buildLeadBeacon("gwk_123", "v-abc", { email: "a@x.com" });
    expect(beacon.value_usd).toBeUndefined();
    expect(beacon.crm_stage).toBeUndefined();
    expect(beacon.self_reported_source).toBeUndefined();
  });
});

describe("getOrCreateVisitorId", () => {
  it("persists the same id across calls, in both the cookie and localStorage", async () => {
    const { getOrCreateVisitorId } = await import("./gwgeo");
    const id1 = getOrCreateVisitorId();
    const id2 = getOrCreateVisitorId();
    expect(id1).toBe(id2);
    expect(id1.length).toBeGreaterThan(0);
    expect(document.cookie).toContain(`gwgeo_vid=${id1}`);
    expect(localStorage.getItem("gwgeo_vid")).toBe(id1);
  });

  it("reuses an id already present in the cookie instead of minting a new one", async () => {
    document.cookie = "gwgeo_vid=existing-id; Path=/;";
    const { getOrCreateVisitorId } = await import("./gwgeo");
    expect(getOrCreateVisitorId()).toBe("existing-id");
  });
});

describe("auto-install (fires on script load)", () => {
  it("beacons a session event using the script's data-key, landing url, referrer, and utm", async () => {
    const fetchMock = mockFetchOk();
    setReferrer("https://www.perplexity.ai/");
    window.history.replaceState({}, "", "/crm?utm_source=chatgpt&utm_medium=referral");
    installScriptTag({ "data-key": "gwk_test123" });

    await import("./gwgeo");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const url = fetchMock.mock.calls[0][0] as string;
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(url).toBe(COLLECT_URL);
    expect(init.method).toBe("POST");
    expect(init.keepalive).toBe(true);
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");

    const body = JSON.parse(init.body as string);
    expect(body.write_key).toBe("gwk_test123");
    expect(body.type).toBe("session");
    expect(typeof body.visitor_id).toBe("string");
    expect(body.visitor_id.length).toBeGreaterThan(0);
    expect(body.landing_url).toBe(window.location.href);
    expect(body.referrer).toBe("https://www.perplexity.ai/");
    expect(body.utm).toEqual({ utm_source: "chatgpt", utm_medium: "referral" });
    expect(typeof body.ts).toBe("string");
  });

  it("uses a data-api override for the API base when present", async () => {
    const fetchMock = mockFetchOk();
    installScriptTag({ "data-key": "gwk_test123", "data-api": "https://staging.example.com" });

    await import("./gwgeo");

    expect(fetchMock.mock.calls[0][0]).toBe("https://staging.example.com/lead-capture/collect");
  });

  it("does nothing (no fetch) and installs a harmless no-op when data-key is missing", async () => {
    const fetchMock = mockFetchOk();
    installScriptTag({});

    await import("./gwgeo");

    expect(fetchMock).not.toHaveBeenCalled();
    expect(typeof window.gwgeo).toBe("function");
    expect(() => window.gwgeo?.("lead", { email: "x@y.com" })).not.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("installs window.gwgeo('lead', {...}) which POSTs a lead event reusing the session's visitor id", async () => {
    const fetchMock = mockFetchOk();
    installScriptTag({ "data-key": "gwk_test123" });

    await import("./gwgeo");
    const sessionBody = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);

    expect(typeof window.gwgeo).toBe("function");
    window.gwgeo?.("lead", { email: "lead@x.com", value: 2500, crmStage: "sql", selfReportedSource: "google" });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const url = fetchMock.mock.calls[1][0] as string;
    const leadInit = fetchMock.mock.calls[1][1] as RequestInit;
    expect(url).toBe(COLLECT_URL);
    const leadBody = JSON.parse(leadInit.body as string);
    expect(leadBody.write_key).toBe("gwk_test123");
    expect(leadBody.type).toBe("lead");
    // same visitor as the auto-fired session beacon, so the backend links lead -> latest session
    expect(leadBody.visitor_id).toBe(sessionBody.visitor_id);
    expect(leadBody.email).toBe("lead@x.com");
    expect(leadBody.value_usd).toBe(2500);
    expect(leadBody.crm_stage).toBe("sql");
    expect(leadBody.self_reported_source).toBe("google");
  });

  it("gwgeo('session', ...) re-fires the pageview beacon", async () => {
    const fetchMock = mockFetchOk();
    installScriptTag({ "data-key": "gwk_test123" });

    await import("./gwgeo");
    window.gwgeo?.("session");

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const secondBody = JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string);
    expect(secondBody.type).toBe("session");
  });
});
