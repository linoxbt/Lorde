import { type BrowserContext, expect, test } from "@playwright/test";

/** Live mode against tests/fixtures/fake-supabase.mjs (see playwright.live.config.ts). */
const FAKE = `http://127.0.0.1:${process.env.FAKE_SUPABASE_PORT ?? 54399}`;

type Call = { method: string; path: string; query: Record<string, string>; body: Record<string, unknown> | null };
const calls = async (): Promise<Call[]> => (await fetch(`${FAKE}/__calls`)).json();
const reset = () => fetch(`${FAKE}/__reset`, { method: "POST" });

/** Sign the browser in as one of the fake's users (a session cookie in @supabase/ssr's format). */
async function signIn(context: BrowserContext, baseURL: string, user: "noorg" | "member" = "noorg") {
  const c = await (await fetch(`${FAKE}/__session?user=${user}`)).json() as { name: string; value: string };
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
  const cspErrors: string[] = [];
  page.on("console", (m) => { if (/Content Security Policy/i.test(m.text())) cspErrors.push(m.text()); });
  const res = await page.goto("/signin", { waitUntil: "networkidle" });
  expect(res?.headers()["content-security-policy"]).toContain("frame-ancestors 'none'");
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
  expect(cspErrors).toEqual([]);
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
  await page.getByRole("button", { name: "Sign out" }).click();  // a form POST: allowed by the CSP's form-action
  await expect(page).toHaveURL(/\/signin$/);
  expect((await calls()).some((c) => c.path === "/auth/v1/logout" && c.query.scope === "local")).toBe(true);
});

test("signed in: /signin forwards to next with its query string", async ({ context, baseURL, page }) => {
  await signIn(context, baseURL!);
  const r = await page.request.get("/signin?next=%2Fconsole%2Fjobs%3Fpage%3D2", { maxRedirects: 0 });
  expect(r.status()).toBe(307);
  expect(r.headers().location).toMatch(/\/console\/jobs\?page=2$/);
  const evil = await page.request.get("/signin?next=%2F%5Cevil.example", { maxRedirects: 0 });
  expect(new URL(evil.headers().location!, "http://x").pathname).toBe("/console");
});

test("an org member's Jobs list renders the real rows, pages through them, and survives a page past the end", async ({ context, baseURL, page }) => {
  await signIn(context, baseURL!, "member");
  await page.goto("/console/jobs");
  await expect(page.getByText("Demo workspace")).toHaveCount(0);
  await expect(page.getByText("30 jobs")).toBeVisible();
  await expect(page.getByText("Page 1 of 2")).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(25);
  await expect(page.locator("tbody tr").first()).toContainText("support-answers.jsonl");
  await expect(page.locator("tbody tr").first()).toContainText("Running");
  await page.getByRole("link", { name: "Next" }).click();
  await expect(page).toHaveURL(/\?page=2$/);
  await expect(page.locator("tbody tr")).toHaveCount(5);
  await expect(page.getByText("purged-batch.jsonl (deleted)")).toBeVisible();
  await expect(page.getByText("acme/chat-pairs")).toBeVisible();
  await expect(page.getByText("Hub dataset")).toHaveCount(0);
  // PostgREST answers 416 PGRST103 past the last row: an empty page with a way back, not the error screen
  const r = await page.goto("/console/jobs?page=3");
  expect(r?.status()).toBe(200);
  await expect(page.getByText("Page 3 is past the end")).toBeVisible();
  await page.getByRole("link", { name: "Go to the last page" }).click();
  await expect(page).toHaveURL(/\?page=2$/);
  await expect(page.locator("tbody tr")).toHaveCount(5);
  const jobCalls = (await calls()).filter((c) => c.path === "/rest/v1/jobs");
  expect(jobCalls.some((c) => c.method === "HEAD"), "the total is recounted after a 416").toBe(true);
  expect(jobCalls.every((c) => c.query.org_id === "eq.10000000-0000-4000-8000-000000000001")).toBe(true);
});

test("an org member's keys, datasets and job detail come from the database", async ({ context, baseURL, page }) => {
  await signIn(context, baseURL!, "member");
  await page.goto("/console/keys");
  await expect(page.getByText("ci-pipeline")).toBeVisible();
  await expect(page.locator("tr").filter({ hasText: "old-laptop" })).toContainText(/Revoked/i);

  await page.goto("/console/datasets");
  await expect(page.locator("tr").filter({ hasText: "support-answers.jsonl" }).getByText("Ready")).toBeVisible();
  const expired = page.locator("tr").filter({ hasText: "old-batch.csv" });
  await expect(expired.getByText("Expired")).toBeVisible();
  await expect(expired.getByRole("link", { name: /Gate it/ })).toHaveCount(0);

  await page.goto("/console/jobs/new?dataset=30000000-0000-4000-8000-000000000003", { waitUntil: "networkidle" });
  await expect(page.locator("#ds option", { hasText: "old-batch.csv" })).toBeDisabled();
  await expect(page.locator("#ds")).toHaveValue("30000000-0000-4000-8000-000000000001");

  await page.goto("/console/jobs/20000000-0000-4000-8000-000000000002");
  await expect(page.getByRole("heading", { name: "Job 20000000" })).toBeVisible();
  await expect(page.getByText("policy-qa.parquet").first()).toBeVisible();

  await page.goto("/console");
  await expect(page.getByText("Acme Research").first()).toBeVisible();
});

test("the reset form recovers from a network error instead of sticking on Saving", async ({ context, baseURL, page }) => {
  await signIn(context, baseURL!, "member");
  await page.goto("/reset-password", { waitUntil: "networkidle" });
  await page.getByLabel("New password", { exact: true }).fill("drop-the-connection");
  await page.getByLabel("Confirm new password").fill("drop-the-connection");
  await page.getByRole("button", { name: "Save password" }).click();
  await expect(page.locator("p[role=alert]")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save password" })).toBeEnabled();
});

test("a PKCE email link opened in another browser says what to do next", async ({ request, page }) => {
  // no code verifier cookie in this context: exactly a link opened on another device
  const signup = await request.get("/auth/callback?code=abc&next=%2Fconsole&flow=signup", { maxRedirects: 0 });
  expect(signup.headers().location).toMatch(/\/signin\?error=email_confirmed$/);
  const magic = await request.get("/auth/callback?code=abc&next=%2Fconsole&flow=magiclink", { maxRedirects: 0 });
  expect(magic.headers().location).toMatch(/\/signin\?error=other_browser$/);
  const reset = await request.get("/auth/callback?code=abc&next=%2Freset-password", { maxRedirects: 0 });
  expect(reset.headers().location).toMatch(/\/signin\?error=other_browser$/);
  await page.goto("/signin?error=email_confirmed");
  await expect(page.locator("p[role=status]").first()).toHaveText("Your email is confirmed: sign in to continue.");
  await expect(page.locator("p[role=alert]")).toHaveCount(0);
  await page.goto("/signin?error=other_browser");
  await expect(page.locator("p[role=alert]")).toContainText("This link was opened in a different browser.");
});

test("every page carries a nonce-based CSP that its own scripts satisfy", async ({ context, baseURL, page }) => {
  await signIn(context, baseURL!, "member");
  const violations: string[] = [];
  page.on("console", (m) => { if (/Content Security Policy|Refused to (load|execute)/i.test(m.text())) violations.push(m.text()); });
  page.on("pageerror", (e) => violations.push(`pageerror: ${e.message}`));
  const nonces = new Set<string>();
  for (const path of ["/", "/docs", "/status", "/console", "/console/jobs", "/console/datasets", "/console/jobs/new",
    "/console/playground", "/console/review", "/console/keys", "/console/settings", "/console/reports"]) {
    const r = await page.goto(path, { waitUntil: "load" });
    const csp = r?.headers()["content-security-policy"] ?? "";
    const nonce = /'nonce-([^']+)'/.exec(csp)?.[1];
    expect(nonce, `${path} has a script nonce`).toBeTruthy();
    expect(csp).toContain("'strict-dynamic'");
    expect(csp).not.toMatch(/script-src[^;]*'unsafe-(inline|eval)'/);
    expect(csp).toContain("frame-ancestors 'none'");
    nonces.add(nonce!);
    // Next put this response's nonce on every script in the HTML (the ones they load later pass by 'strict-dynamic')
    const tags = (await r!.text()).match(/<script\b[^>]*>/g) ?? [];
    expect(tags.length).toBeGreaterThan(0);
    expect(tags.filter((t) => !t.includes(`nonce="${nonce}"`)), `${path}: scripts without the nonce`).toEqual([]);
    // and the page hydrated: client components respond
    await expect(page.locator("body")).toBeVisible();
  }
  expect(nonces.size, "a fresh nonce per response").toBe(12);
  // hydration works under the policy: the menu (a client component) opens
  await page.goto("/docs", { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Open menu" }).click();
  await expect(page.getByRole("dialog", { name: "Menu" })).toBeVisible();
  expect(violations).toEqual([]);
});
