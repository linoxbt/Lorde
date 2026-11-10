import { type NextRequest, NextResponse } from "next/server";

import { env } from "@/lib/env";

export const dynamic = "force-dynamic";
const ALLOWED = /^v1\/(judge\/(reward|grounding|batch)|uploads|jobs(\/[0-9a-f-]{36}(\/(results|cancel))?)?|models|usage)$/;

/** Forwards allow-listed Gateway calls with the caller's own key (x-lorde-key) or, for self-hosting, LORDE_API_KEY. */
async function forward(req: NextRequest, ctx: RouteContext<"/api/gateway/[...path]">) {
  const { path } = await ctx.params;
  const p = path.join("/");
  if (!env.gatewayUrl) return NextResponse.json({ error: { code: "not_configured", message: "LORDE_GATEWAY_URL is not set" } }, { status: 503 });
  if (!ALLOWED.test(p)) return NextResponse.json({ error: { code: "not_found", message: "no such route" } }, { status: 404 });
  const key = req.headers.get("x-lorde-key") || env.apiKey;
  if (!key) return NextResponse.json({ error: { code: "unauthenticated", message: "connect an API key first" } }, { status: 401 });
  const r = await fetch(`${env.gatewayUrl}/${p}${req.nextUrl.search}`, {
    method: req.method,
    headers: { authorization: `Bearer ${key}`, "content-type": "application/json" },
    body: req.method === "GET" ? undefined : await req.text(),
    cache: "no-store",
  });
  return new NextResponse(await r.text(), {
    status: r.status,
    headers: { "content-type": r.headers.get("content-type") ?? "application/json",
      "x-request-id": r.headers.get("x-request-id") ?? "" },
  });
}

export { forward as GET, forward as POST };
