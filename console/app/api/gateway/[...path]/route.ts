import { type NextRequest, NextResponse } from "next/server";

import { apiError, sessionUserId, unauthenticated } from "@/lib/api";
import { env, supabaseEnabled } from "@/lib/env";
import { ALLOWED, timeoutFor } from "@/lib/relay";

export const dynamic = "force-dynamic";
/** Seconds: longer than the relay's slowest timeout (RELAY_MAX_DURATION_S in lib/relay.ts; a unit test checks both). */
export const maxDuration = 130;
const PASS_HEADERS = ["content-type", "retry-after", "x-request-id", "x-lorde-model", "x-lorde-template",
  "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"];

/**
 * Forwards allow-listed Gateway calls with the caller's own key (x-lorde-key). With Supabase accounts configured the
 * caller must be signed in, and only their own key is used. LORDE_API_KEY is a fallback for single-tenant
 * self-hosting without Supabase only.
 */
async function forward(req: NextRequest, ctx: RouteContext<"/api/gateway/[...path]">) {
  const { path } = await ctx.params;
  const p = path.join("/");
  if (!ALLOWED.test(p)) return apiError(404, "not_found", "no such route");
  if (supabaseEnabled() && !(await sessionUserId())) return unauthenticated();
  if (!env.gatewayUrl) return apiError(503, "not_configured", "The Gateway is not configured for this Console.");
  const key = req.headers.get("x-lorde-key") || (supabaseEnabled() ? "" : env.apiKey);
  if (!key) return apiError(401, "no_api_key", "Connect an API key first.");
  let r: Response, body: ArrayBuffer;
  try {
    r = await fetch(`${env.gatewayUrl}/${p}${req.nextUrl.search}`, {
      method: req.method,
      headers: relayHeaders(req, key),
      body: req.method === "GET" ? undefined : await req.text(),
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutFor(p)),
    });
    body = await r.arrayBuffer();
  } catch (e) {
    const timedOut = e instanceof DOMException && (e.name === "TimeoutError" || e.name === "AbortError");
    return timedOut
      ? apiError(504, "gateway_timeout", "The Gateway did not answer in time. Try again.")
      : apiError(502, "gateway_unreachable", "The Gateway is unreachable right now. Check the status page and try again.");
  }
  const headers = new Headers();
  for (const h of PASS_HEADERS) {
    const v = r.headers.get(h);
    if (v) headers.set(h, v);
  }
  if (!headers.has("content-type")) headers.set("content-type", "application/json");
  return new NextResponse(body, { status: r.status, headers });
}

export { forward as GET, forward as POST };

/** The caller's key, plus (with a relay secret) the end user's IP so the Gateway's per-IP limit applies per user. */
function relayHeaders(req: NextRequest, key: string): Record<string, string> {
  const h: Record<string, string> = { authorization: `Bearer ${key}`, "content-type": "application/json" };
  if (env.relaySecret) {
    const ip = (req.headers.get("x-forwarded-for") ?? "").split(",")[0]!.trim() || req.headers.get("x-real-ip") || "";
    if (ip) {
      h["x-lorde-relay-secret"] = env.relaySecret;
      h["x-lorde-client-ip"] = ip;
    }
  }
  return h;
}
