import { NextResponse } from "next/server";

import { supabaseEnabled } from "@/lib/env";

/** Supabase redirects here after a magic link or GitHub sign-in; exchange the code for a session. */
export async function GET(request: Request) {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const next = url.searchParams.get("next") ?? "/console";
  const safeNext = next.startsWith("/console") ? next : "/console";
  if (code && supabaseEnabled()) {
    const { supabaseServer } = await import("@/lib/supabase/server");
    const sb = await supabaseServer();
    const { error } = await sb.auth.exchangeCodeForSession(code);
    if (error) return NextResponse.redirect(new URL(`/signin?error=${encodeURIComponent(error.message)}`, url.origin));
  }
  return NextResponse.redirect(new URL(safeNext, url.origin));
}
