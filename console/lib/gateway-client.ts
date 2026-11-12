"use client";

import { type ErrorBody, friendlyGatewayError } from "./gateway-errors.ts";
import type { Scope } from "./scopes";

/**
 * Browser-side Gateway access. The user's own API key lives in sessionStorage for this tab only and is
 * forwarded by /api/gateway to the Gateway; it is never stored server-side.
 */
const KEY = "lorde.apiKey";

export const getKey = () => (typeof window === "undefined" ? "" : sessionStorage.getItem(KEY) ?? "");
export const setKey = (k: string) => (k ? sessionStorage.setItem(KEY, k.trim()) : sessionStorage.removeItem(KEY));

export async function gatewayFetch(path: string, init: RequestInit = {}, key = getKey()) {
  const headers = new Headers(init.headers);
  if (key) headers.set("x-lorde-key", key);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return fetch(`/api/gateway${path}`, { ...init, headers });
}

/** The Gateway's (or the Console relay's) error message, made friendly for known codes (quota, busy model,
 * rate limits), or a fallback; never throws. */
export async function errorMessage(r: Response, fallback = `Request failed (${r.status})`): Promise<string> {
  const j = await r.json().catch(() => null) as ErrorBody;
  return friendlyGatewayError(r.status, j, r.headers.get("retry-after"), fallback);
}

const NIL = "00000000-0000-0000-0000-000000000000";
/**
 * A cheap call per scope: each answers 403 when the key lacks the scope, and otherwise 400/404/200 without doing
 * work. The Gateway checks the key's scope before it validates the body, so an empty judge request is refused with
 * 400 (no model call, nothing charged) for a key that has `judge`, and with 403 for one that lacks it.
 */
export const PROBES: Record<Scope, [string, RequestInit]> = {
  judge: ["/v1/judge/reward", { method: "POST", body: "{}" }],
  "jobs:read": [`/v1/jobs/${NIL}`, {}],
  "jobs:write": [`/v1/jobs/${NIL}/cancel`, { method: "POST", body: "{}" }],
  "usage:read": ["/v1/usage?days=1", {}],
};

/** Check a key before connecting it: it must authenticate, and hold every scope this screen needs. */
export async function checkKey(key: string, need: Scope[]): Promise<{ ok: true } | { ok: false; message: string }> {
  const unavailable = (r: Response) => r.status >= 500 || r.status === 429;
  let r: Response;
  try {
    r = await gatewayFetch("/v1/models", {}, key);
  } catch {
    return { ok: false, message: "Could not reach the Console. Check your connection and try again." };
  }
  if (r.status === 401) {
    const msg = await errorMessage(r);
    return { ok: false, message: /sign in/i.test(msg) ? "Your Console session has ended. Sign in again." : "This key is not recognised. Check it, or create a new one under API keys." };
  }
  if (r.status === 403) {  // /v1/models needs no scope: a 403 here is a revoked key
    return { ok: false, message: "This key has been revoked. Create a new one under API keys." };
  } else if (unavailable(r)) {
    return { ok: false, message: await errorMessage(r, "The Gateway is unavailable right now. Try again shortly.") };
  } else if (!r.ok) {
    return { ok: false, message: await errorMessage(r) };
  }
  const missing: string[] = [];
  for (const s of need) {
    const [path, init] = PROBES[s];
    const p = await gatewayFetch(path, init, key).catch(() => null);
    if (!p || unavailable(p)) {
      if (p?.status === 503) return { ok: false, message: "Jobs are not enabled on this Gateway." };
      return { ok: false, message: "The Gateway is unavailable right now. Try again shortly." };
    }
    if (p.status === 403) missing.push(s);
  }
  if (missing.length) {
    return { ok: false, message: `This key lacks the ${missing.join(" and ")} scope${missing.length > 1 ? "s" : ""} needed here. Create a key with ${missing.length > 1 ? "them" : "it"} under API keys.` };
  }
  return { ok: true };
}
