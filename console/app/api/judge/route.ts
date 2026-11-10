import { NextResponse } from "next/server";
import { z } from "zod";

import { simulate } from "@/lib/demo-judge";

const Body = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("reward"), instruction: z.string().min(1).max(32_000), response: z.string().max(32_000) }),
  z.object({ kind: z.literal("grounding"), source_text: z.string().min(1).max(24_000), question: z.string().min(1).max(32_000),
    answer: z.string().max(32_000) }),
]);

/** Demo-mode judge for the playground (simulated; live mode calls the Gateway instead). */
export async function POST(req: Request) {
  const parsed = Body.safeParse(await req.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ error: { code: "invalid_request", message: parsed.error.issues[0]?.message ?? "invalid" } }, { status: 400 });
  }
  const { kind, ...fields } = parsed.data;
  return NextResponse.json({ simulated: true, ...simulate(kind, fields as Record<string, string>) });
}
