/**
 * The /api/gateway relay's routes and timeouts (pure: unit-tested in tests/unit/relay.test.ts).
 *
 * Timeouts, consistently: the relay gives a Gateway call RELAY_SLOW_MS (judging on the CPU model, jobs, uploads) or
 * RELAY_FAST_MS, then answers JSON 504. The route's `maxDuration` (Vercel function limit, seconds) must exceed the
 * slow timeout, or Vercel kills the function first and the browser gets a bare platform error instead of the 504.
 * The browser itself sets no shorter timeout. Gateway side, LORDE_UPSTREAM_TIMEOUT plus LORDE_UPSTREAM_QUEUE_TIMEOUT_S
 * should stay at or below RELAY_SLOW_MS for Console users (docs/DEPLOYMENT.md).
 */
export const RELAY_SLOW_MS = 120_000;
export const RELAY_FAST_MS = 30_000;
/** Seconds; app/api/gateway/[...path]/route.ts repeats it as a literal (Next reads segment config statically). */
export const RELAY_MAX_DURATION_S = 130;

export const ALLOWED = /^v1\/(judge\/(reward|grounding|batch)|uploads|jobs(\/[0-9a-f-]{36}(\/(results|cancel))?)?|models|usage)$/;

export const timeoutFor = (p: string) =>
  (p.startsWith("v1/judge") || p.startsWith("v1/jobs") || p === "v1/uploads" ? RELAY_SLOW_MS : RELAY_FAST_MS);
