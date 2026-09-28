const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const TOKEN_STORAGE_KEY = "kb_access_token";
const EXPIRY_SKEW_SECONDS = 60;

let pending: Promise<string | null> | null = null;

function readStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_STORAGE_KEY, token);
    else localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // storage unavailable (private mode): the token lives for this page only
  }
}

function isExpired(token: string): boolean {
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return typeof payload.exp === "number" && payload.exp - EXPIRY_SKEW_SECONDS < Date.now() / 1000;
  } catch {
    return true;
  }
}

let memoryToken: string | null = null;

/** Current bearer token without network access (may be null or expired). */
export function getAccessToken(): string | null {
  return memoryToken || readStoredToken() || process.env.NEXT_PUBLIC_API_TOKEN || null;
}

/**
 * A valid bearer token. On the public demo (backend AUTH_MODE=demo) a visitor
 * without one gets a private guest session automatically — no signup. Concurrent
 * callers share a single guest request.
 */
export async function ensureAccessToken(): Promise<string | null> {
  const existing = getAccessToken();
  if (existing && !isExpired(existing)) return existing;
  if (!pending) {
    pending = (async () => {
      try {
        const res = await fetch(`${API_URL}/api/v1/auth/guest`, { method: "POST" });
        if (!res.ok) return null; // private deployment (AUTH_MODE=jwt) or rate limited
        const data = await res.json();
        memoryToken = data.access_token;
        storeToken(data.access_token);
        return data.access_token as string;
      } catch {
        return null;
      } finally {
        pending = null;
      }
    })();
  }
  return pending;
}

/** Forget a token the server rejected (e.g. an expired guest session). */
export function clearAccessToken() {
  memoryToken = null;
  storeToken(null);
}

export async function authHeaders(): Promise<Record<string, string>> {
  const token = await ensureAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
