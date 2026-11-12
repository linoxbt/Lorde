import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { ALLOWED, IDEMPOTENCY_KEY, RELAY_FAST_MS, RELAY_MAX_DURATION_S, RELAY_SLOW_MS, timeoutFor } from "../../lib/relay.ts";

test("the relay's function limit outlasts its slowest Gateway timeout", () => {
  const src = readFileSync(new URL("../../app/api/gateway/[...path]/route.ts", import.meta.url), "utf8");
  const m = /export const maxDuration = (\d+);/.exec(src);
  assert.ok(m, "route.ts exports a literal maxDuration");
  assert.equal(Number(m[1]), RELAY_MAX_DURATION_S);
  assert.ok(RELAY_MAX_DURATION_S * 1000 >= RELAY_SLOW_MS + 5_000, "time for the session check and the 504 answer");
  assert.ok(RELAY_MAX_DURATION_S <= 300, "Vercel's maximum");
  assert.equal(timeoutFor("v1/judge/reward"), RELAY_SLOW_MS);
  assert.equal(timeoutFor("v1/jobs"), RELAY_SLOW_MS);
  assert.equal(timeoutFor("v1/models"), RELAY_FAST_MS);
});

test("only allow-listed Gateway routes are relayed", () => {
  for (const p of ["v1/judge/reward", "v1/judge/batch", "v1/uploads", "v1/jobs", "v1/jobs/00000000-0000-0000-0000-000000000000/cancel", "v1/models", "v1/usage"]) {
    assert.ok(ALLOWED.test(p), p);
  }
  for (const p of ["v1/chat/completions", "metrics", "v1/jobs/x/../../metrics", "v1/models/lorde", "readyz"]) assert.ok(!ALLOWED.test(p), p);
});

test("the relay forwards only Idempotency-Keys the Gateway accepts", () => {
  assert.ok(IDEMPOTENCY_KEY.test("5f0c7f9e-2b7a-4d1e-9c3f-0a1b2c3d4e5f"));
  assert.ok(IDEMPOTENCY_KEY.test("0123456789abcdef0123456789abcdef"));
  for (const bad of ["short", "a b c d e f g h", "x".repeat(129), "key\r\nx-evil: 1"]) assert.ok(!IDEMPOTENCY_KEY.test(bad), bad);
});
