import { defineConfig, devices } from "@playwright/test";

/**
 * Live-mode e2e: the production build with Supabase "configured", pointed at a small fake Supabase
 * (tests/fixtures/fake-supabase.mjs), and a Gateway URL nothing listens on. Built into .next-live so it
 * never replaces the normal .next build.   pnpm test:e2e:live
 */
const PORT = Number(process.env.LIVE_PORT ?? 3100);
const FAKE = Number(process.env.FAKE_SUPABASE_PORT ?? 54399);
const FAKE_URL = `http://127.0.0.1:${FAKE}`;

export default defineConfig({
  testDir: "tests/e2e-live",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: `http://localhost:${PORT}`, trace: "off", ...devices["Desktop Chrome"] },
  webServer: [
    { command: `node tests/fixtures/fake-supabase.mjs ${FAKE}`, url: `${FAKE_URL}/__calls`, reuseExistingServer: false, timeout: 20_000 },
    {
      command: `pnpm exec next build && pnpm exec next start --port ${PORT}`,
      url: `http://localhost:${PORT}/signin`,
      reuseExistingServer: false,
      timeout: 300_000,
      env: {
        NEXT_DIST_DIR: ".next-live",
        NEXT_PUBLIC_SUPABASE_URL: FAKE_URL,
        NEXT_PUBLIC_SUPABASE_ANON_KEY: "sb_publishable_fake_for_tests",
        NEXT_PUBLIC_SITE_URL: `http://localhost:${PORT}`,
        LORDE_GATEWAY_URL: "http://127.0.0.1:54397",  // nothing listens here, so the relay must answer 502
        LORDE_KEY_PEPPER: "live-e2e-pepper-live-e2e-pepper-0123456789",
        LORDE_API_KEY: "lorde_sk_operator_key_that_must_never_be_used",
      },
    },
  ],
});
