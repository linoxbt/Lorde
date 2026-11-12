import { NextResponse } from "next/server";

import { env } from "@/lib/env";
import { type Check, gatewayChecks, memoize, servedModel, summarize } from "@/lib/status";

export const dynamic = "force-dynamic";

/** Public and unauthenticated: one Gateway probe pair per 10 s per instance, and the CDN may share the answer too. */
const TTL_MS = 10_000;
const CACHE = "public, max-age=0, s-maxage=10, stale-while-revalidate=20";

async function probe(path: string): Promise<{ ok: boolean; status: number | null; ms: number; body: unknown }> {
  const t = performance.now();
  try {
    // /readyz checks the model, database and Redis with 3 s timeouts of its own: leave it time to answer.
    const r = await fetch(`${env.gatewayUrl}${path}`, { cache: "no-store", signal: AbortSignal.timeout(5000) });
    return { ok: r.ok, status: r.status, ms: Math.round(performance.now() - t), body: await r.json().catch(() => null) };
  } catch {
    return { ok: false, status: null, ms: Math.round(performance.now() - t), body: null };
  }
}

const check = memoize(async () => {
  if (!env.gatewayUrl) {  // health checks need no key: the URL is enough
    const checks: Check[] = [
      { name: "Gateway", state: "demo", detail: "Not connected: set LORDE_GATEWAY_URL to monitor it." },
      { name: "Lorde Serve", state: "demo", detail: "Checked through the Gateway's /readyz." },
    ];
    return { summary: summarize(checks), checks, model: servedModel([], env.modelRevision) };
  }
  const [health, ready] = await Promise.all([probe("/healthz"), probe("/readyz")]);
  const checks = gatewayChecks(health, ready);
  // /readyz and /healthz are public and report no model today; servedModel picks one up if they ever do.
  return { summary: summarize(checks), checks, model: servedModel([ready.body, health.body], env.modelRevision),
    checked_at: new Date().toISOString() };
}, TTL_MS);

export async function GET() {
  return NextResponse.json(await check(), { headers: { "cache-control": CACHE } });
}
