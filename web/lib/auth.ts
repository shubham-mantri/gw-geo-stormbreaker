// Client-side session store. Tenancy is derived from the token by the backend
// and returned in the login payload; the UI stores it as-is and NEVER lets the
// user select or override a tenant (ui-spec §5). There is deliberately no
// `setTenant` — the only way tenant changes is a fresh login.

export type Role = "owner" | "admin" | "editor" | "viewer";

export type Session = {
  accessToken: string;
  refreshToken: string;
  role: Role;
  /** Derived from the token by the backend — read-only on the client. */
  tenantId: string;
};

/** Raw shape returned by POST /auth/login. */
export type LoginResponse = {
  access_token: string;
  refresh_token: string;
  role: Role;
  tenant_id: string;
};

const STORAGE_KEY = "gw_geo_session";
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

export function getSession(): Session | null {
  if (!isBrowser()) return null;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Session;
  } catch {
    return null;
  }
}

export function setSession(session: Session): void {
  if (!isBrowser()) return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
}

export function clearSession(): void {
  if (!isBrowser()) return;
  window.localStorage.removeItem(STORAGE_KEY);
}

/** Bearer token for API calls, or null when unauthenticated. */
export function getToken(): string | null {
  return getSession()?.accessToken ?? null;
}

/** Tenant id from the session (backend-derived, read-only — ui-spec §5). */
export function getTenantId(): string | null {
  return getSession()?.tenantId ?? null;
}

export function getRole(): Role | null {
  return getSession()?.role ?? null;
}

export function isAuthenticated(): boolean {
  return getToken() !== null;
}

function sessionFromResponse(data: LoginResponse): Session {
  return {
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    role: data.role,
    tenantId: data.tenant_id,
  };
}

/**
 * POST /auth/login → persist and return the session. The tenant arrives inside
 * the response and is stored verbatim; the client cannot choose it.
 */
export async function login(email: string, password: string): Promise<Session> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    throw new Error(`Login failed (${res.status})`);
  }
  const data = (await res.json()) as LoginResponse;
  const session = sessionFromResponse(data);
  setSession(session);
  return session;
}

export function logout(): void {
  clearSession();
}
