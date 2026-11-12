import assert from "node:assert/strict";
import { test } from "node:test";

import { friendlyGatewayError } from "../../lib/gateway-errors.ts";

test("Gateway errors map to friendly messages", () => {
  const quota = friendlyGatewayError(429, { error: { code: "quota_exceeded", message: "daily judgment quota of 5000 reached" } });
  assert.match(quota, /organization has used today's judgment quota/);
  assert.match(friendlyGatewayError(429, { error: { code: "quota_exceeded", message: "" } }, "3600"), /resets in about 60 min/);
  assert.equal(friendlyGatewayError(429, { error: { code: "rate_limited", message: "model busy" } }, "5"),
    "Lorde is busy with other requests right now. Try again in 5 s.");
  assert.match(friendlyGatewayError(429, { error: { code: "rate_limited", message: "Model busy, retry" } }), /a few seconds/);
  assert.equal(friendlyGatewayError(429, { error: { code: "rate_limited", message: "rate limit of 60 judgments/minute exceeded" } }, "12"),
    "Rate limit of 60 judgments/minute exceeded. Try again in 12 s.");
  assert.equal(friendlyGatewayError(404, { error: { code: "not_found", message: "no such job" } }), "no such job");
  assert.equal(friendlyGatewayError(502, null, null, "fallback"), "fallback");
  assert.equal(friendlyGatewayError(500, undefined), "Request failed (500)");
});
