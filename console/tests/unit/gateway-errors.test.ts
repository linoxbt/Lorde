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

test("audit-2 Gateway error codes map to friendly messages", () => {
  assert.match(friendlyGatewayError(429, { error: { code: "quota_exceeded",
    message: "this organization's daily quota of 50 judgments is used up (50 today)" } }), /\(50 judgments a day\.\)/);
  assert.match(friendlyGatewayError(429, { error: { code: "capacity_exceeded", message: "x" } }, "7200"),
    /service-wide daily capacity.*about 2 h.*not charged/);
  assert.match(friendlyGatewayError(429, { error: { code: "storage_quota_exceeded", message: "x" } }), /storage limit/);
  assert.match(friendlyGatewayError(422, { error: { code: "unsupported_dataset", message: "x" } }), /one file/);
  assert.match(friendlyGatewayError(405, { error: { code: "method_not_allowed", message: "x" } }), /does not accept/);
});
