/**
 * What /auth/confirm and /auth/callback do with an email or OAuth link, as a pure function of its query string
 * (unit-tested in tests/unit/auth.test.ts; lib/auth-link.ts runs the plan against Supabase).
 *
 * Two link shapes reach us:
 * - `token_hash` + `type` (the email templates in supabase/templates): verified server-side with verifyOtp, so the
 *   link works in any browser on any device;
 * - `code` (PKCE: GitHub sign-in, or an email sent with the older `{{ .ConfirmationURL }}` templates). Supabase has
 *   already verified the email before it redirects with the code, but only the browser that asked for the email
 *   holds the code verifier. Opened anywhere else, the exchange fails with `pkce_code_verifier_not_found`; the
 *   `flow` we add to emailRedirectTo says which email it was, so the user gets the right next step.
 */
import { codeForAuthRedirect, type SigninErrorCode } from "./auth-errors.ts";
import { destination } from "./safe-next.ts";

/** Every email OTP type Supabase sends (`email` is the newer name for signup/magiclink confirmations). */
export const OTP_TYPES = ["signup", "invite", "magiclink", "recovery", "email_change", "email"] as const;
export type OtpType = (typeof OTP_TYPES)[number];
/** Which email a PKCE redirect came from (the `flow` query parameter the sign-in form adds). */
export const FLOWS = ["signup", "magiclink", "recovery"] as const;
export type Flow = (typeof FLOWS)[number];

export type AuthPlan =
  | { kind: "fail"; code: SigninErrorCode }
  | { kind: "go"; to: string }
  | { kind: "otp"; type: OtpType; tokenHash: string; to: string }
  | { kind: "exchange"; code: string; to: string; flow: Flow | null };

const isOtpType = (t: string | null): t is OtpType => OTP_TYPES.includes(t as OtpType);
const asFlow = (f: string | null): Flow | null => (FLOWS.includes(f as Flow) ? (f as Flow) : null);

/** Decide what to do with a link. `enabled` is false in demo mode (no Supabase): just go to the destination. */
export function planAuth(q: URLSearchParams, origin: string, enabled: boolean): AuthPlan {
  if (q.get("error") || q.get("error_code")) return { kind: "fail", code: codeForAuthRedirect(q.get("error"), q.get("error_code"), flowOf(q)) };
  const type = q.get("type");
  const to = destination(q.get("next"), origin, type ?? (flowOf(q) === "recovery" ? "recovery" : null));
  if (!enabled) return { kind: "go", to };
  const tokenHash = q.get("token_hash"), code = q.get("code");
  if (tokenHash) return isOtpType(type) ? { kind: "otp", type, tokenHash, to } : { kind: "fail", code: "link_invalid" };
  if (code) return { kind: "exchange", code, to, flow: flowOf(q) };
  return { kind: "fail", code: "link_invalid" };
}

/** The flow of a PKCE redirect: the explicit `flow`, else a reset link (next=/reset-password), else unknown. */
export function flowOf(q: URLSearchParams): Flow | null {
  const f = asFlow(q.get("flow"));
  if (f) return f;
  const next = q.get("next") ?? "";
  return /(^|\/)reset-password(\?|$)/.test(next.replace(/^https?:\/\/[^/]+/i, "")) ? "recovery" : null;
}

/** A failed verifyOtp: expired links get "request a new one", everything else "invalid or already used". */
export function codeForOtpError(errorCode: string | undefined): SigninErrorCode {
  return errorCode === "otp_expired" || errorCode === "flow_state_expired" ? "link_expired" : "link_invalid";
}

/**
 * A failed PKCE exchange. Without the code verifier (another browser or device), Supabase has nevertheless
 * confirmed the address: a sign-up confirmation needs only a sign-in; a sign-in or reset link must be requested
 * again (or opened in the browser that asked for it).
 */
export function codeForExchangeError(errorCode: string | undefined, flow: Flow | null): SigninErrorCode {
  if (errorCode === "pkce_code_verifier_not_found" || errorCode === "bad_code_verifier") {
    return flow === "signup" ? "email_confirmed" : "other_browser";
  }
  if (errorCode === "flow_state_expired" || errorCode === "otp_expired") return "link_expired";
  return "session_failed";
}
