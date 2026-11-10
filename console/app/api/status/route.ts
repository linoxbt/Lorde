import { NextResponse } from "next/server";

import { env } from "@/lib/env";

export const dynamic = "force-dynamic";

type Check = { name: string; state: "ok" | "degraded" | "down" | "demo"; detail: string; ms?: number };

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
    return NextResponse.json({ summary: { state: "demo", label: "Demo mode" }, checks });
  }
  const [health, ready] = await Promise.all([probe("/healthz"), probe("/readyz")]);
  const checks: Check[] = [
    { name: "Gateway", state: health.ok ? "ok" : "down", detail: health.ok ? "Responding" : "Unreachable", ms: health.ms },
    { name: "Lorde Serve", state: ready.ok ? "ok" : "degraded",
      detail: ready.ok ? "Model ready" : "Model not ready or circuit open", ms: ready.ms },
  ];
  const state = checks.every((c) => c.state === "ok") ? "ok" : checks.some((c) => c.state === "down") ? "down" : "degraded";
  const label = { ok: "All systems operational", degraded: "Degraded performance", down: "Service disruption" }[state];
  return NextResponse.json({ summary: { state, label }, checks });
}
