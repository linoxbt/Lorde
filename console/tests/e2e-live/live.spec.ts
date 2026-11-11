import { type BrowserContext, expect, test } from "@playwright/test";

/** Live mode against tests/fixtures/fake-supabase.mjs (see playwright.live.config.ts). */
const FAKE = `http://127.0.0.1:${process.env.FAKE_SUPABASE_PORT ?? 54399}`;

type Call = { method: string; path: string; query: Record<string, string>; body: Record<string, unknown> | null };
const calls = async (): Promise<Call[]> => (await fetch(`${FAKE}/__calls`)).json();
const reset = () => fetch(`${FAKE}/__reset`, { method: "POST" });

/** Sign the browser in as the fake's "noorg" user (a session cookie in @supabase/ssr's format). */
async function signIn(context: BrowserContext, baseURL: string) {
  const c = await (await fetch(`${FAKE}/__session?user=noorg`)).json() as { name: string; value: string };
  await context.addCookies([{ name: c.name, value: c.value, url: baseURL }]);
}

test.beforeEach(async () => { await reset(); });

test("signed out: /console redirects to /signin and keeps the path and query in next", async ({ request }) => {
  const r = await request.get("/console/jobs/new?dataset=abc-123", { maxRedirects: 0 });
  expect(r.status()).toBe(307);
  const to = new URL(r.headers().location!, "http://x");
  expect(to.pathname).toBe("/signin");
  expect(to.searchParams.get("next")).toBe("/console/jobs/new?dataset=abc-123");
});

test("the API relays require a session: 401 JSON, and the operator key is never used", async ({ request }) => {
  for (const [method, path] of [["GET", "/api/gateway/v1/models"], ["POST", "/api/gateway/v1/judge/reward"], ["POST", "/api/judge"],
    ["POST", "/api/keys"], ["DELETE", "/api/keys?id=00000000-0000-4000-8000-000000000009"]] as const) {
    const r = await request.fetch(path, { method, data: method === "POST" ? { kind: "reward", instruction: "x", response: "y" } : undefined });
    expect(r.status(), `${method} ${path}`).toBe(401);
    expect((await r.json()).error.code).toBe("unauthenticated");
  }
});

test("signed in, the relay answers JSON 502 when the Gateway is down", async ({ context, baseURL, page }) => {
  await signIn(context, baseURL!);
  const r = await page.request.get("/api/gateway/v1/models", { headers: { "x-lorde-key": "lorde_sk_test" } });
  expect(r.status()).toBe(502);
  expect((await r.json()).error.code).toBe("gateway_unreachable");
  expect(r.headers()["x-request-id"]).toBeUndefined();
  const none = await page.request.get("/api/gateway/v1/models");  // no key of the user's own: LORDE_API_KEY is not a fallback
  expect(none.status()).toBe(401);
  expect((await none.json()).error.code).toBe("no_api_key");
});

test("sign-in errors: only fixed messages, never text from the URL", async ({ page }) => {
  await page.goto("/signin?error=link_expired");
  await expect(page.locator("p[role=alert]")).toHaveText("That link has expired. Request a new one below.");
  await page.goto(`/signin?error=${encodeURIComponent("Your account is locked. Call +1 555 0100 now")}`);
  await expect(page.locator("p[role=alert]")).toHaveText("Sign-in failed. Try again.");
  await expect(page.getByText("Call +1 555")).toHaveCount(0);
});

test("sign-in form maps Supabase errors and offers to resend the confirmation", async ({ page }) => {
  await page.goto("/signin", { waitUntil: "networkidle" });
  await page.getByLabel("Email").fill("someone@example.com");
  await page.getByLabel("Password").fill("wrong-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.locator("p[role=alert]")).toHaveText("Wrong email or password.");
  await page.getByLabel("Email").fill("unconfirmed@example.com");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.locator("p[role=alert]")).toContainText("Confirm your email first");
  await page.getByRole("button", { name: "Resend confirmation email" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Sent a new confirmation email" })).toBeVisible();
  const resend = (await calls()).find((c) => c.path === "/auth/v1/resend");
  expect(resend?.body).toMatchObject({ type: "signup", email: "unconfirmed@example.com" });
});

test("email links: token_hash is verified with verifyOtp, then the user lands on next", async ({ request }) => {
  const ok = await request.get("/auth/confirm?token_hash=good-hash&type=email&next=/console/keys", { maxRedirects: 0 });
  expect(ok.status()).toBe(307);
  expect(new URL(ok.headers().location!, "http://x").pathname).toBe("/console/keys");
  expect(ok.headers()["set-cookie"]).toMatch(/sb-127-auth-token/);
  const verify = (await calls()).filter((c) => c.path === "/auth/v1/verify");
  expect(verify).toHaveLength(1);
  expect(verify[0]!.body).toMatchObject({ token_hash: "good-hash", type: "email" });

  const viaRedirectTo = await request.get(`/auth/confirm?token_hash=good-hash&type=signup&next=${encodeURIComponent("http://localhost:3100/auth/callback?next=/console/jobs/new?dataset=d1")}`, { maxRedirects: 0 });
  expect(new URL(viaRedirectTo.headers().location!, "http://x").pathname + new URL(viaRedirectTo.headers().location!, "http://x").search)
    .toBe("/console/jobs/new?dataset=d1");
  const recovery = await request.get("/auth/confirm?token_hash=good-hash&type=recovery&next=/console", { maxRedirects: 0 });
  expect(new URL(recovery.headers().location!, "http://x").pathname).toBe("/reset-password");
  const evil = await request.get("/auth/confirm?token_hash=good-hash&type=email&next=//evil.example/console", { maxRedirects: 0 });
  expect(new URL(evil.headers().location!, "http://x").pathname).toBe("/console");

  const expired = await request.get("/auth/confirm?token_hash=old-hash&type=email", { maxRedirects: 0 });
  expect(expired.headers().location).toContain("/signin?error=link_expired");
  const badType = await request.get("/auth/confirm?token_hash=good-hash&type=bogus", { maxRedirects: 0 });
  expect(badType.headers().location).toContain("/signin?error=link_invalid");
});

test("a signed-in user in no organization sees the onboarding page, not a crash", async ({ context, baseURL, page }) => {
  await signIn(context, baseURL!);
  const r = await page.goto("/console");
  expect(r?.status()).toBe(200);
  await expect(page).toHaveURL(/\/welcome$/);
  await expect(page.getByText("Your account isn't in an organization yet")).toBeVisible();
  await expect(page.getByText("noorg@example.com")).toBeVisible();
  for (const path of ["/console/jobs", "/console/settings", "/console/jobs/00000000-0000-4000-8000-000000000001"]) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/welcome$/);
  }
});

test("signed in: /signin forwards to next with its query string", async ({ context, baseURL, page }) => {
  await signIn(context, baseURL!);
  const r = await page.request.get("/signin?next=%2Fconsole%2Fjobs%3Fpage%3D2", { maxRedirects: 0 });
  expect(r.status()).toBe(307);
  expect(r.headers().location).toMatch(/\/console\/jobs\?page=2$/);
  const evil = await page.request.get("/signin?next=%2F%5Cevil.example", { maxRedirects: 0 });
  expect(new URL(evil.headers().location!, "http://x").pathname).toBe("/console");
});
