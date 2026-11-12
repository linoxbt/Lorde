import "server-only";

import type { EmailOtpType } from "@supabase/supabase-js";
import { NextResponse } from "next/server";

import type { SigninErrorCode } from "./auth-errors";
import { codeForExchangeError, codeForOtpError, planAuth } from "./auth-flow";
import { supabaseEnabled } from "./env";

const fail = (origin: string, code: SigninErrorCode) => NextResponse.redirect(new URL(`/signin?error=${code}`, origin));

/**
 * Finish an email link or OAuth sign-in (the decision is lib/auth-flow.ts's planAuth):
 * - `token_hash` + `type` (works on any device: the email templates link here), verified with verifyOtp;
 * - `code` (PKCE: OAuth, or a link opened in the browser that requested it), exchanged for a session. Opened in
 *   another browser, the exchange cannot work: the user is told their email is confirmed, or to request a new link.
 * Errors redirect to /signin with a fixed error code, never Supabase's text.
 */
export async function finishAuth(request: Request): Promise<NextResponse> {
  const url = new URL(request.url);
  const plan = planAuth(url.searchParams, url.origin, supabaseEnabled());
  if (plan.kind === "fail") return fail(url.origin, plan.code);
  if (plan.kind === "go") return NextResponse.redirect(new URL(plan.to, url.origin));
  const { supabaseServer } = await import("./supabase/server");
  const sb = await supabaseServer();
  if (plan.kind === "otp") {
    const { error } = await sb.auth.verifyOtp({ type: plan.type as EmailOtpType, token_hash: plan.tokenHash });
    if (error) return fail(url.origin, codeForOtpError(error.code));
  } else {
    const { error } = await sb.auth.exchangeCodeForSession(plan.code);
    if (error) return fail(url.origin, codeForExchangeError(error.code, plan.flow));
  }
  return NextResponse.redirect(new URL(plan.to, url.origin));
}
