import { NextResponse } from "next/server";
import { z } from "zod";

import { sessionUserId, unauthenticated } from "@/lib/api";
import { simulate } from "@/lib/demo-judge";
import { supabaseEnabled } from "@/lib/env";

const Body = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("reward"), instruction: z.string().min(1).max(32_000), response: z.string().max(32_000) }),
  z.object({ kind: z.literal("grounding"), source_text: z.string().min(1).max(24_000), question: z.string().min(1).max(32_000),
    answer: z.string().max(32_000) }),
]);

/**
 * Simulated judge for the playground when no API key is connected: a transparent heuristic, no model, no Gateway.
 * Open in demo mode (no Supabase); with accounts configured it needs a signed-in session like the rest of the Console.
 */
export async function POST(req: Request) {
  if (supabaseEnabled() && !(await sessionUserId())) return unauthenticated();
  const parsed = Body.safeParse(await req.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ error: { code: "invalid_request", message: parsed.error.issues[0]?.message ?? "invalid" } }, { status: 400 });
  }
  const { kind, ...fields } = parsed.data;
  return NextResponse.json({ simulated: true, ...simulate(kind, fields as Record<string, string>) });
}
