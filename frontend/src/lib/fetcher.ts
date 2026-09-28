import { authHeaders, clearAccessToken } from "./auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const fetcher = async (endpoint: string) => {
  let res = await fetch(`${API_URL}${endpoint}`, { headers: await authHeaders() });
  if (res.status === 401) {
    clearAccessToken();
    res = await fetch(`${API_URL}${endpoint}`, { headers: await authHeaders() });
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
};
