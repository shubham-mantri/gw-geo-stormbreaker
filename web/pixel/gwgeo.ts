/**
 * gwgeo.js — first-party lead-capture beacon (m2-design.md §6; TRD §6.1; M2-T05).
 *
 * The client-side half of the lead-capture pixel. `web/pixel/gwgeo.ts` is meant to be built to
 * `web/public/gwgeo.js` (build step is future work — out of scope here) and installed on a
 * customer's site via the `<script src=".../gwgeo.js" data-key="gwk_...">` snippet minted by
 * `GET /lead-capture/snippet` (see `src/gw_geo/api/routers/settings.py`). Deliberately
 * dependency-free (no imports from the rest of `web/`) so the built bundle stays tiny and can be
 * hosted/versioned independently of the dashboard app.
 *
 * Wire contract — binding; mirrors `src/gw_geo/api/routers/leadcapture.py::CollectBody` (the
 * actual request body the endpoint parses) and, in turn,
 * `src/gw_geo/attribution/ingest.py::SessionEvent` / `LeadEvent` (what it's ingested into):
 *
 *   POST {API_BASE}/lead-capture/collect
 *   { write_key, type: "session" | "lead", visitor_id,
 *     // session fields
 *     landing_url, referrer?, utm?, user_agent?,
 *     // lead fields
 *     email?, value_usd?, crm_stage?, self_reported_source?,
 *     ts? }                                                    -> 202 { ok: true }
 *
 * `write_key` is a public, per-brand, write-only token (`ingest.mint_write_key` /
 * `resolve_write_key`): it resolves server-side to exactly one `(tenant_id, brand_id)` and is
 * carried as a body field (never a header/query param, and the server never echoes tenant data
 * back) — a leaked key only lets someone write bogus sessions/leads into that one brand, nothing
 * more (m2-design.md §6, §12).
 *
 * Behavior:
 *   - On load, reads its own `<script data-key="...">` (via `document.currentScript`, with a
 *     `querySelector` fallback for contexts where `currentScript` is unavailable, e.g. a module
 *     script), resolves/creates a first-party `visitor_id` (cookie + localStorage), and beacons a
 *     `session` event: landing URL, `document.referrer`, UTM params parsed from `location.search`,
 *     and UA.
 *   - Installs `window.gwgeo(command, payload)` for the customer's own page JS (typically a
 *     form-submit handler) to call: `"lead"` posts a conversion/lead event reusing the same
 *     `visitor_id`, so the backend links it to this visitor's latest session
 *     (`ingest.ingest_lead`); `"session"` re-fires the pageview beacon (e.g. after an SPA route
 *     change).
 *
 * White-hat (PRD NG1): this is a first-party analytics beacon, not a fingerprinting device — it
 * identifies returning visitors via one ordinary first-party cookie, sends only the fields listed
 * above, and never reads or reports anything about other sites, other pixels, or the visitor's
 * device beyond `navigator.userAgent`.
 */

const COLLECT_PATH = "/lead-capture/collect";

// No config knob for this exists server-side yet (flagged in `settings.py`'s
// `_PIXEL_SNIPPET_SRC` comment as out of scope for M2-T05/T16, and again here). `data-api` on the
// install `<script>` tag is an escape hatch (self-hosted API / staging) until a real one lands;
// production installs omit it and get this default.
const DEFAULT_API_BASE = "https://api.gwgeo.io";

// "Session id" in product language (m2-design §6) — the one identity concept the wire contract
// actually carries is `visitor_id` (a session/`Session` row is per-*beacon*, minted server-side).
// This is the first-party cookie/localStorage key that persists it across pageviews.
const VISITOR_COOKIE = "gwgeo_vid";
const VISITOR_STORAGE_KEY = "gwgeo_vid";
const COOKIE_MAX_AGE_S = 60 * 60 * 24 * 365; // 1 year

/**
 * A beaconed pageview. Field names/shape mirror the `session` fields of `leadcapture.CollectBody`
 * (equivalently `ingest.SessionEvent`, minus `tenant_id`/`brand_id`, which the server resolves
 * from `write_key` — the beacon never knows them).
 */
export interface SessionBeacon {
  write_key: string;
  type: "session";
  visitor_id: string;
  landing_url: string;
  referrer?: string;
  utm: Record<string, string>;
  user_agent?: string;
  ts: string;
}

/** A captured lead/conversion. Field names/shape mirror `ingest.LeadEvent`. */
export interface LeadBeacon {
  write_key: string;
  type: "lead";
  visitor_id: string;
  email?: string;
  value_usd?: number;
  crm_stage?: string;
  self_reported_source?: string;
  ts: string;
}

/**
 * The public, ergonomic payload for `gwgeo("lead", {...})` — translated onto `LeadBeacon`'s
 * `ingest.LeadEvent` wire field names (`value` -> `value_usd`, etc.) before it hits the wire.
 */
export interface LeadPayload {
  email?: string;
  value?: number;
  crmStage?: string;
  selfReportedSource?: string;
}

export type GwgeoCommand = "lead" | "session";
export type Gwgeo = (command: GwgeoCommand, payload?: LeadPayload) => void;

declare global {
  interface Window {
    gwgeo?: Gwgeo;
  }
}

// --- pure helpers (no DOM writes; unit-testable directly) --------------------------------------

/**
 * Extract `utm_*` query params from a `location.search`-shaped string (leading `?` optional),
 * e.g. `"?utm_source=chatgpt&ref=x"` -> `{ utm_source: "chatgpt" }`. Keys are matched
 * case-insensitively and lower-cased in the result so `UTM_Source`/`utm_source` don't produce two
 * different keys downstream; any param starting with `utm_` is forwarded (not just the five
 * "standard" ones), since the backend stores this as an opaque `dict[str, str]`.
 */
export function parseUtm(search: string): Record<string, string> {
  const utm: Record<string, string> = {};
  for (const [key, value] of new URLSearchParams(search).entries()) {
    if (value !== "" && key.toLowerCase().startsWith("utm_")) {
      utm[key.toLowerCase()] = value;
    }
  }
  return utm;
}

/**
 * Build the `session` beacon body for `POST /lead-capture/collect`. Empty/absent `referrer` and
 * `userAgent` are omitted (`undefined`, dropped by `JSON.stringify`) rather than sent as `""`,
 * matching `CollectBody`'s `str | None` fields.
 */
export function buildBeacon(
  writeKey: string,
  opts: {
    href: string;
    referrer?: string;
    search?: string;
    userAgent?: string;
    visitorId: string;
  },
): SessionBeacon {
  return {
    write_key: writeKey,
    type: "session",
    visitor_id: opts.visitorId,
    landing_url: opts.href,
    referrer: opts.referrer ? opts.referrer : undefined,
    utm: parseUtm(opts.search ?? ""),
    user_agent: opts.userAgent ? opts.userAgent : undefined,
    ts: new Date().toISOString(),
  };
}

/**
 * Build the `lead` beacon body for `POST /lead-capture/collect` from the public
 * `gwgeo("lead", ...)` payload shape, translated onto `ingest.LeadEvent`'s wire field names.
 */
export function buildLeadBeacon(
  writeKey: string,
  visitorId: string,
  payload: LeadPayload,
): LeadBeacon {
  return {
    write_key: writeKey,
    type: "lead",
    visitor_id: visitorId,
    email: payload.email,
    value_usd: payload.value,
    crm_stage: payload.crmStage,
    self_reported_source: payload.selfReportedSource,
    ts: new Date().toISOString(),
  };
}

// --- first-party visitor id: cookie, with localStorage as a secondary store --------------------

function getCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  for (const part of document.cookie ? document.cookie.split(";") : []) {
    const eq = part.indexOf("=");
    if (eq === -1) continue;
    if (part.slice(0, eq).trim() === name) return decodeURIComponent(part.slice(eq + 1));
  }
  return null;
}

function setCookie(name: string, value: string, maxAgeSeconds: number): void {
  try {
    const secure = typeof location !== "undefined" && location.protocol === "https:" ? "; Secure" : "";
    document.cookie =
      `${name}=${encodeURIComponent(value)}; Max-Age=${maxAgeSeconds}; Path=/; SameSite=Lax${secure}`;
  } catch {
    // Cookies blocked (privacy mode / disabled storage): the id resolved for this pageview still
    // gets used (falls back to localStorage or a fresh id each time); it just won't persist.
  }
}

function readStorage(key: string): string | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  try {
    if (typeof localStorage !== "undefined") localStorage.setItem(key, value);
  } catch {
    // best-effort only, as above
  }
}

function generateVisitorId(): string {
  const c: Crypto | undefined = typeof crypto === "undefined" ? undefined : crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  if (c && typeof c.getRandomValues === "function") {
    const bytes = c.getRandomValues(new Uint8Array(16));
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  // Last-resort fallback for environments without any `crypto` global. Not security-sensitive:
  // this is a first-party analytics identifier, not an auth token.
  return `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`;
}

/**
 * Resolve this visitor's persistent id, creating and storing (cookie + localStorage) one if
 * neither store has it yet. The same id is reused by the session beacon and every later
 * `gwgeo("lead", ...)` call so the backend can link a lead to its visitor's latest session
 * (`ingest.ingest_lead` matches on `visitor_id`).
 */
export function getOrCreateVisitorId(): string {
  const existing = getCookie(VISITOR_COOKIE) ?? readStorage(VISITOR_STORAGE_KEY);
  const id = existing ?? generateVisitorId();
  setCookie(VISITOR_COOKIE, id, COOKIE_MAX_AGE_S);
  writeStorage(VISITOR_STORAGE_KEY, id);
  return id;
}

// --- transport -----------------------------------------------------------------------------

function postBeacon(apiBase: string, body: SessionBeacon | LeadBeacon): void {
  try {
    // `fetch` + `keepalive` rather than `navigator.sendBeacon`: the backend requires a real
    // `Content-Type: application/json` header to parse the body (verified against
    // `leadcapture.py` — FastAPI only JSON-decodes the body when the request's media type is
    // `application/json`; a `text/plain` beacon-style body, the usual CORS-preflight-avoidance
    // trick, gets rejected with a 422), and `sendBeacon` cannot set that header reliably across
    // browsers. `keepalive: true` gives `fetch` the same "survive page unload" property.
    void fetch(`${apiBase}${COLLECT_PATH}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      keepalive: true,
    }).catch(() => {
      // Fire-and-forget analytics beacon: a network/CORS failure must never reach the host page.
    });
  } catch {
    // ditto, for a synchronous throw (e.g. an environment with no usable `fetch`)
  }
}

// --- install -------------------------------------------------------------------------------

function currentScriptEl(): HTMLScriptElement | null {
  if (typeof document === "undefined") return null;
  const current = document.currentScript as HTMLScriptElement | null;
  if (current && current.tagName === "SCRIPT") return current;
  // `document.currentScript` is always `null` for module scripts. Fall back to the last
  // `data-key`-bearing script tag on the page (there should only ever be one install snippet).
  const candidates = document.querySelectorAll<HTMLScriptElement>("script[data-key]");
  return candidates.length > 0 ? candidates[candidates.length - 1] : null;
}

function pageContext(): { href: string; referrer?: string; search: string; userAgent?: string } {
  return {
    href: location.href,
    referrer: document.referrer || undefined,
    search: location.search,
    userAgent: typeof navigator === "undefined" ? undefined : navigator.userAgent,
  };
}

function init(): void {
  if (typeof document === "undefined") return; // non-browser environment (e.g. SSR import)

  const scriptEl = currentScriptEl();
  const writeKey = scriptEl?.getAttribute("data-key");
  if (!writeKey) {
    // Misconfigured install (missing/blank `data-key`): no writes are authorized without a key,
    // so stay silent on the wire, but still install a harmless no-op `gwgeo` so a customer's
    // `gwgeo("lead", ...)` call site never throws, and surface the misconfiguration to their
    // console so the integrator notices it.
    console.warn("gwgeo: no data-key found on the install <script> tag; lead capture is disabled.");
    window.gwgeo = () => {};
    return;
  }

  const apiBase = scriptEl?.getAttribute("data-api") || DEFAULT_API_BASE;

  const sendSession = (): void => {
    postBeacon(apiBase, buildBeacon(writeKey, { ...pageContext(), visitorId: getOrCreateVisitorId() }));
  };

  sendSession();

  window.gwgeo = (command, payload = {}) => {
    if (command === "lead") {
      postBeacon(apiBase, buildLeadBeacon(writeKey, getOrCreateVisitorId(), payload));
    } else if (command === "session") {
      sendSession();
    }
  };
}

init();
