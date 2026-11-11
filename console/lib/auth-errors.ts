/**
 * Fixed, user-facing auth messages. The sign-in page only ever shows these: never text taken from the URL.
 */
export const SIGNIN_ERRORS = {
  link_invalid: "That sign-in link is invalid or was already used. Request a new one.",
  link_expired: "That link has expired. Request a new one below.",
  oauth_failed: "Signing in with GitHub did not complete. Try again.",
  access_denied: "Sign-in was cancelled.",
  session_failed: "We could not start your session. Try again.",
} as const;
export type SigninErrorCode = keyof typeof SIGNIN_ERRORS;
export const GENERIC_SIGNIN_ERROR = "Sign-in failed. Try again.";

/** The message for an `?error=` code on /signin; unknown codes get the generic message. */
export function signinErrorMessage(code: unknown): string | undefined {
  if (typeof code !== "string" || !code) return undefined;
  return Object.hasOwn(SIGNIN_ERRORS, code) ? SIGNIN_ERRORS[code as SigninErrorCode] : GENERIC_SIGNIN_ERROR;
}

/** Map a Supabase redirect error (error / error_code query params) to one of our codes. */
export function codeForAuthRedirect(error: string | null, errorCode: string | null): SigninErrorCode {
  if (errorCode === "otp_expired" || errorCode === "flow_state_expired") return "link_expired";
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
