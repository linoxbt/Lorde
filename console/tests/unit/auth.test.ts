import assert from "node:assert/strict";
import { test } from "node:test";

import { codeForAuthRedirect, friendlyAuthError, GENERIC_SIGNIN_ERROR, SIGNIN_ERRORS, signinErrorMessage, signinMessage } from "../../lib/auth-errors.ts";
import { codeForExchangeError, codeForOtpError, flowOf, OTP_TYPES, planAuth } from "../../lib/auth-flow.ts";
import { destination, safeNext } from "../../lib/safe-next.ts";

test("safeNext keeps Console paths with their query string", () => {
  assert.equal(safeNext("/console"), "/console");
  assert.equal(safeNext("/console/jobs/new?dataset=abc-123"), "/console/jobs/new?dataset=abc-123");
  assert.equal(safeNext("/console/jobs?page=2&x=1"), "/console/jobs?page=2&x=1");
});

test("safeNext rejects anything that could leave the Console", () => {
  for (const bad of [
    undefined, "", "console", "https://evil.example/console", "//evil.example/console", "/\\evil.example", "\\\\evil",
    "/console/../evil", "/consoleevil", "/\t/evil.example", "/console\n//evil", "javascript:alert(1)", "/signin", "/%2e%2e/evil",
  ]) {
    assert.equal(safeNext(bad), "/console", String(bad));
  }
  assert.equal(safeNext("/console/../reset-password", ["/console", "/reset-password"]), "/reset-password");
});

test("destination unwraps {{ .RedirectTo }} and forces recovery to the reset page", () => {
  const o = "https://lorde.example";
  assert.equal(destination("/console/keys", o), "/console/keys");
  assert.equal(destination(`${o}/auth/callback?next=/console/jobs/new?dataset=d1`, o), "/console/jobs/new?dataset=d1");
  assert.equal(destination(`${o}/auth/callback`, o), "/console");
  assert.equal(destination(o, o), "/console");
  assert.equal(destination("https://evil.example/auth/callback?next=/console/x", o), "/console");
  assert.equal(destination("/console", o, "recovery"), "/reset-password");
  assert.equal(destination(null, o), "/console");
});

test("sign-in page shows only fixed messages", () => {
  assert.equal(signinErrorMessage("link_expired"), SIGNIN_ERRORS.link_expired);
  assert.equal(signinErrorMessage("Your account was hacked, call +1 555"), GENERIC_SIGNIN_ERROR);
  assert.equal(signinErrorMessage("toString"), GENERIC_SIGNIN_ERROR);
  assert.equal(signinErrorMessage(undefined), undefined);
  assert.equal(codeForAuthRedirect("access_denied", "otp_expired"), "link_expired");
  assert.equal(codeForAuthRedirect("server_error", "bad_oauth_state"), "oauth_failed");
  assert.equal(codeForAuthRedirect("access_denied", null), "access_denied");
  assert.equal(codeForAuthRedirect("x", "y"), "link_invalid");
});

test("Supabase auth errors map to friendly text", () => {
  const e = (message: string, code?: string) => Object.assign(new Error(message), { code });
  assert.equal(friendlyAuthError(e("Invalid login credentials", "invalid_credentials")).text, "Wrong email or password.");
  assert.equal(friendlyAuthError(e("Email not confirmed")).unconfirmed, true);
  assert.match(friendlyAuthError(e("Email address \"a@b.co\" not authorized")).text, /can't send email/);
  assert.match(friendlyAuthError(e("Email address not authorized")).text, /can't send email/);
  assert.match(friendlyAuthError(e("email rate limit exceeded", "over_email_send_rate_limit")).text, /Too many emails/);
  assert.equal(friendlyAuthError(e("TypeError: fetch failed at internal/xyz")).text, "Something went wrong. Try again.");
  assert.match(friendlyAuthError(e("captcha protection: request disallowed (invalid-input-response)", "captcha_failed")).text, /security check/);
});

const O = "https://lorde.example";
const plan = (qs: string, enabled = true) => planAuth(new URLSearchParams(qs), O, enabled);

test("/auth/confirm verifies every email type Supabase sends, and lands each on the right page", () => {
  // The five templates in supabase/templates, plus `email` (Supabase's newer name for signup/magic-link confirmation).
  const cases: [string, string][] = [
    ["signup", "/console"], ["email", "/console"], ["magiclink", "/console"],
    ["recovery", "/reset-password"], ["email_change", "/console/settings"], ["invite", "/reset-password?invited=1"],
  ];
  for (const [type, to] of cases) {
    const next = type === "email_change" ? "/console/settings" : "/console";
    assert.deepEqual(plan(`token_hash=h1&type=${type}&next=${encodeURIComponent(next)}`), { kind: "otp", type, tokenHash: "h1", to }, type);
  }
  for (const t of OTP_TYPES) assert.equal(plan(`token_hash=h&type=${t}`).kind, "otp", t);
  // {{ .RedirectTo }} unwraps to the inner next; the recovery type wins over any next
  assert.deepEqual(plan(`token_hash=h&type=signup&next=${encodeURIComponent(`${O}/auth/callback?next=/console/keys&flow=signup`)}`),
    { kind: "otp", type: "signup", tokenHash: "h", to: "/console/keys" });
  assert.equal((plan("token_hash=h&type=recovery&next=/console/keys") as { to: string }).to, "/reset-password");
  assert.deepEqual(plan("token_hash=h&type=bogus"), { kind: "fail", code: "link_invalid" });
  assert.deepEqual(plan("token_hash=h"), { kind: "fail", code: "link_invalid" });
  assert.deepEqual(plan(""), { kind: "fail", code: "link_invalid" });
  assert.deepEqual(plan("next=/console/keys", false), { kind: "go", to: "/console/keys" });
});

test("a PKCE link opened in another browser says what to do next instead of failing", () => {
  assert.deepEqual(plan("code=c1&next=%2Fconsole&flow=signup"), { kind: "exchange", code: "c1", to: "/console", flow: "signup" });
  assert.equal(codeForExchangeError("pkce_code_verifier_not_found", "signup"), "email_confirmed");
  assert.equal(SIGNIN_ERRORS.email_confirmed, "Your email is confirmed: sign in to continue.");
  assert.equal(signinMessage("email_confirmed")?.tone, "notice");
  for (const flow of ["magiclink", "recovery", null] as const) {
    assert.equal(codeForExchangeError("pkce_code_verifier_not_found", flow), "other_browser", String(flow));
  }
  assert.match(SIGNIN_ERRORS.other_browser, /opened in a different browser\. Request a new link here, or open it in the browser you used/);
  assert.equal(signinMessage("other_browser")?.tone, "error");
  // older reset emails carry no flow, but their next is the reset page
  assert.equal(flowOf(new URLSearchParams("code=c&next=%2Freset-password")), "recovery");
  assert.equal((plan("code=c&next=%2Freset-password") as { to: string }).to, "/reset-password");
  assert.equal(flowOf(new URLSearchParams("code=c&next=%2Fconsole&flow=evil")), null);
  assert.equal(codeForExchangeError("flow_state_expired", "signup"), "link_expired");
  assert.equal(codeForExchangeError("unexpected_failure", null), "session_failed");
  assert.equal(codeForOtpError("otp_expired"), "link_expired");
  assert.equal(codeForOtpError("bad_jwt"), "link_invalid");
  // Supabase redirecting back with a verifier error gets the same messages
  assert.deepEqual(plan("error=invalid_request&error_code=pkce_code_verifier_not_found&flow=signup"), { kind: "fail", code: "email_confirmed" });
  assert.equal(codeForAuthRedirect("server_error", "pkce_code_verifier_not_found"), "other_browser");
});
