import { NextResponse } from "next/server";

import { env } from "@/lib/env";
import { type Check, servedModel, summarize } from "@/lib/status";

export const dynamic = "force-dynamic";

async function probe(path: string): Promise<{ ok: boolean; ms: number; body: unknown }> {
  const t = performance.now();
  try {
    const r = await fetch(`${env.gatewayUrl}${path}`, { cache: "no-store", signal: AbortSignal.timeout(4000) });
    return { ok: r.ok, ms: Math.round(performance.now() - t), body: await r.json().catch(() => null) };
  } catch {
    return { ok: false, ms: Math.round(performance.now() - t), body: null };
  }
}

export async function GET() {
  if (!env.gatewayUrl) {  // health checks need no key: the URL is enough
    const checks: Check[] = [
      { name: "Gateway", state: "demo", detail: "Not connected: set LORDE_GATEWAY_URL to monitor it." },
      { name: "Lorde Serve", state: "demo", detail: "Checked through the Gateway's /readyz." },
      { name: "Job queue", state: "demo", detail: "Checked through the Gateway once jobs are configured." },
    ];
    return NextResponse.json({ summary: summarize(checks), checks, model: servedModel([], env.modelRevision) });
  }
  const [health, ready] = await Promise.all([probe("/healthz"), probe("/readyz")]);
  const checks: Check[] = [
    { name: "Gateway", state: health.ok ? "ok" : "down", detail: health.ok ? "Responding" : "Unreachable", ms: health.ms },
    { name: "Lorde Serve", state: ready.ok ? "ok" : "degraded",
      detail: ready.ok ? "Model server responding" : "Model server not ready or circuit open", ms: ready.ms },
  ];
  // /readyz and /healthz are public and report no model today; servedModel picks one up if they ever do.
  return NextResponse.json({ summary: summarize(checks), checks,
    model: servedModel([ready.body, health.body], env.modelRevision) });
}
