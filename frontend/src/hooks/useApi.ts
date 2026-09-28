"use client";

import { useCallback } from "react";
import { authHeaders, clearAccessToken } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export function useApi() {
  const apiFetch = useCallback(
    async (endpoint: string, options: RequestInit = {}): Promise<Response> => {
      const send = async () => {
        const headers = new Headers(options.headers);
        for (const [key, value] of Object.entries(await authHeaders())) {
          if (!headers.has(key)) headers.set(key, value);
        }
        return fetch(`${API_URL}${endpoint}`, { ...options, headers });
      };
      const response = await send();
      if (response.status !== 401) return response;
      // Expired or revoked session: get a fresh one and retry once.
      clearAccessToken();
      return send();
    },
    []
  );

  /** XHR (for upload progress) with auth headers already applied. */
  const createXhr = useCallback(
    async (method: string, endpoint: string): Promise<XMLHttpRequest> => {
      const headers = await authHeaders();
      const xhr = new XMLHttpRequest();
      xhr.open(method, `${API_URL}${endpoint}`);
      for (const [key, value] of Object.entries(headers)) {
        xhr.setRequestHeader(key, value);
      }
      return xhr;
    },
    []
  );

  return { apiFetch, createXhr };
}
