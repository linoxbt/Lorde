import { NextResponse } from "next/server";

import { supabaseEnabled } from "@/lib/env";

const ALLOWED = ["/console", "/reset-password"];

/** Supabase redirects here after email confirmation, a magic link, a password reset or GitHub; exchange the code for a session. */
export async function GET(request: Request) {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const next = url.searchParams.get("next") ?? "/console";
  const safeNext = ALLOWED.some((p) => next === p || next.startsWith(`${p}/`)) ? next : "/console";
  const oauthError = url.searchParams.get("error_description");
  if (oauthError) return NextResponse.redirect(new URL(`/signin?error=${encodeURIComponent(oauthError)}`, url.origin));
  if (code && supabaseEnabled()) {
    const { supabaseServer } = await import("@/lib/supabase/server");
    const sb = await supabaseServer();
    const { error } = await sb.auth.exchangeCodeForSession(code);
    if (error) return NextResponse.redirect(new URL(`/signin?error=${encodeURIComponent(error.message)}`, url.origin));
  }
  return NextResponse.redirect(new URL(safeNext, url.origin));
}
