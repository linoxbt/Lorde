import assert from "node:assert/strict";
import { test } from "node:test";

import { buildCsp, newNonce } from "../../lib/csp.ts";

const parse = (csp: string) => Object.fromEntries(csp.split("; ").map((d) => { const [k, ...v] = d.split(" "); return [k!, v]; }));

test("scripts need this response's nonce; no unsafe-inline or eval in production", () => {
  const p = parse(buildCsp({ nonce: "abc123", supabaseUrl: "https://ref.supabase.co", apiUrl: "https://api.example.com/v1" }));
  assert.deepEqual(p["script-src"]!.slice(0, 2), ["'nonce-abc123'", "'strict-dynamic'"]);
  assert.ok(!p["script-src"]!.some((s) => /unsafe/.test(s)));
  assert.deepEqual(p["object-src"], ["'none'"]);
  assert.deepEqual(p["frame-ancestors"], ["'none'"]);
  assert.deepEqual(p["base-uri"], ["'self'"]);
  assert.deepEqual(p["form-action"], ["'self'"]);
  assert.deepEqual(p["frame-src"], ["'none'"]);
  assert.deepEqual(p["connect-src"], ["'self'", "https://ref.supabase.co", "wss://ref.supabase.co", "https://api.example.com"]);
});

test("Turnstile and dev mode add only what they need", () => {
  const t = parse(buildCsp({ nonce: "n", turnstile: true }));
  assert.deepEqual(t["frame-src"], ["https://challenges.cloudflare.com"]);
  assert.ok(t["connect-src"]!.includes("https://challenges.cloudflare.com"));
  const d = parse(buildCsp({ nonce: "n", dev: true }));
  assert.ok(d["script-src"]!.includes("'unsafe-eval'"));
  // junk URLs are left out rather than breaking the header
  assert.deepEqual(parse(buildCsp({ nonce: "n", supabaseUrl: "not a url", apiUrl: "" }))["connect-src"], ["'self'"]);
});

test("nonces are fresh and long enough", () => {
  const a = newNonce(), b = newNonce();
  assert.notEqual(a, b);
  assert.ok(Buffer.from(a, "base64").length === 16);
});
