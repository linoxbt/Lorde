"use client";

/**
 * Browser-side Gateway access. The user's own API key lives in sessionStorage for this tab only and is
 * forwarded by /api/gateway to the Gateway; it is never stored server-side.
 */
const KEY = "lorde.apiKey";

export const getKey = () => (typeof window === "undefined" ? "" : sessionStorage.getItem(KEY) ?? "");
export const setKey = (k: string) => (k ? sessionStorage.setItem(KEY, k.trim()) : sessionStorage.removeItem(KEY));

export async function gatewayFetch(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  const k = getKey();
  if (k) headers.set("x-lorde-key", k);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return fetch(`/api/gateway${path}`, { ...init, headers });
}
