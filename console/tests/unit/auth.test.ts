import assert from "node:assert/strict";
import { test } from "node:test";

import { codeForAuthRedirect, friendlyAuthError, GENERIC_SIGNIN_ERROR, SIGNIN_ERRORS, signinErrorMessage } from "../../lib/auth-errors.ts";
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
});
