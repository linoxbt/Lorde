/**
 * Fixed, user-facing auth messages. The sign-in page only ever shows these: never text taken from the URL.
 */
export const SIGNIN_ERRORS = {
  link_invalid: "That sign-in link is invalid or was already used. Request a new one.",
  link_expired: "That link has expired. Request a new one below.",
  oauth_failed: "Signing in with GitHub did not complete. Try again.",
  access_denied: "Sign-in was cancelled.",
  session_failed: "We could not start your session. Try again.",
  // A PKCE link opened in another browser or device: Supabase has confirmed the address, but cannot sign in here.
  email_confirmed: "Your email is confirmed: sign in to continue.",
  other_browser: "This link was opened in a different browser. Request a new link here, or open it in the browser you used to request it.",
} as const;
/** Codes that are good news or a next step, shown as a notice rather than an error. */
const NOTICES: ReadonlySet<SigninErrorCode> = new Set(["email_confirmed"]);
export type SigninErrorCode = keyof typeof SIGNIN_ERRORS;
export const GENERIC_SIGNIN_ERROR = "Sign-in failed. Try again.";

/** The message for an `?error=` code on /signin; unknown codes get the generic message. */
export function signinErrorMessage(code: unknown): string | undefined {
  return signinMessage(code)?.text;
}

/** The message for an `?error=` code, and whether it is an error or a notice (e.g. "your email is confirmed"). */
export function signinMessage(code: unknown): { text: string; tone: "error" | "notice" } | undefined {
  if (typeof code !== "string" || !code) return undefined;
  if (!Object.hasOwn(SIGNIN_ERRORS, code)) return { text: GENERIC_SIGNIN_ERROR, tone: "error" };
  const c = code as SigninErrorCode;
  return { text: SIGNIN_ERRORS[c], tone: NOTICES.has(c) ? "notice" : "error" };
}

/**
 * Map a Supabase redirect error (error / error_code query params) to one of our codes. `flow` is the email the link
 * came from, when known (see lib/auth-flow.ts).
 */
export function codeForAuthRedirect(error: string | null, errorCode: string | null, flow: "signup" | "magiclink" | "recovery" | null = null): SigninErrorCode {
  if (errorCode === "otp_expired" || errorCode === "flow_state_expired") return "link_expired";
  if (errorCode === "pkce_code_verifier_not_found") return flow === "signup" ? "email_confirmed" : "other_browser";
  if (errorCode && /oauth|provider/i.test(errorCode)) return "oauth_failed";
  if (error === "access_denied" && !errorCode) return "access_denied";
  return "link_invalid";
}

export type FriendlyAuthError = { text: string; unconfirmed?: boolean };

/** Supabase Auth errors (message and, where present, error code) to plain messages. */
export function friendlyAuthError(err: unknown): FriendlyAuthError {
  const code = typeof err === "object" && err && "code" in err ? String((err as { code?: unknown }).code ?? "") : "";
  const m = err instanceof Error ? err.message : typeof err === "string" ? err : "";
  if (code === "invalid_credentials" || /invalid login credentials/i.test(m)) return { text: "Wrong email or password." };
  if (code === "captcha_failed" || /captcha/i.test(m)) return { text: "The security check failed or expired. Complete it again and retry." };
  if (code === "email_not_confirmed" || /email not confirmed/i.test(m)) {
    return { text: "Confirm your email first: open the link we sent you, or send a new one.", unconfirmed: true };
  }
  if (code === "over_email_send_rate_limit" || /email rate limit/i.test(m)) {
    return { text: "Too many emails were sent recently. Wait a few minutes and try again, or continue with GitHub." };
  }
  if (code === "email_address_not_authorized" || /email address .*not authorized/i.test(m)) {
    return { text: "We can't send email to this address yet. Continue with GitHub, or try again later." };
  }
  if (code === "signup_disabled" || /signups not allowed|otp.*disabled/i.test(m)) {
    return { text: "No account with this email yet. Create an account first." };
  }
  if (code === "user_already_exists" || /already registered/i.test(m)) {
    return { text: "An account with this email already exists. Sign in instead." };
  }
  if (code === "same_password" || /different from the old/i.test(m)) return { text: "Choose a password different from your current one." };
  if (code === "weak_password" || /password should/i.test(m)) return { text: "Choose a stronger password (at least 8 characters)." };
  if (code === "over_request_rate_limit" || /rate limit|too many/i.test(m)) return { text: "Too many attempts. Wait a minute and try again." };
  if (code === "email_address_invalid" || /invalid.*email|email.*invalid/i.test(m)) return { text: "Enter a valid email address." };
  return { text: "Something went wrong. Try again." };
}
