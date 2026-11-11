import "server-only";

import type { EmailOtpType } from "@supabase/supabase-js";
import { NextResponse } from "next/server";

import { codeForAuthRedirect, type SigninErrorCode } from "./auth-errors";
import { supabaseEnabled } from "./env";
import { destination } from "./safe-next";

const OTP_TYPES = new Set<EmailOtpType>(["signup", "invite", "magiclink", "recovery", "email_change", "email"]);

const fail = (origin: string, code: SigninErrorCode) => NextResponse.redirect(new URL(`/signin?error=${code}`, origin));

/**
 * Finish an email link or OAuth sign-in. Handles both flows Supabase can send:
 * - `token_hash` + `type` (works on any device: the email templates link here), verified with verifyOtp;
 * - `code` (PKCE: OAuth, or a link opened in the browser that requested it), exchanged for a session.
 * Errors redirect to /signin with a fixed error code, never Supabase's text.
 */
export async function finishAuth(request: Request): Promise<NextResponse> {
  const url = new URL(request.url);
  const q = url.searchParams;
  const tokenHash = q.get("token_hash");
  const type = q.get("type");
  const code = q.get("code");
  if (q.get("error") || q.get("error_code")) return fail(url.origin, codeForAuthRedirect(q.get("error"), q.get("error_code")));
  const to = destination(q.get("next"), url.origin, type);
  if (!supabaseEnabled()) return NextResponse.redirect(new URL(to, url.origin));
  if (!tokenHash && !code) return fail(url.origin, "link_invalid");
  const { supabaseServer } = await import("./supabase/server");
  const sb = await supabaseServer();
  if (tokenHash) {
    if (!type || !OTP_TYPES.has(type as EmailOtpType)) return fail(url.origin, "link_invalid");
    const { error } = await sb.auth.verifyOtp({ type: type as EmailOtpType, token_hash: tokenHash });
    if (error) return fail(url.origin, error.code === "otp_expired" ? "link_expired" : "link_invalid");
  } else if (code) {
    const { error } = await sb.auth.exchangeCodeForSession(code);
    if (error) {
      // A PKCE link opened in another browser has no code verifier: tell the user to request a new link here.
      return fail(url.origin, error.code === "flow_state_expired" || error.code === "otp_expired" ? "link_expired" : "session_failed");
    }
  }
  return NextResponse.redirect(new URL(to, url.origin));
}
