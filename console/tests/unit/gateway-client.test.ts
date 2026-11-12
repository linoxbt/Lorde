import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { checkKey } from "../../lib/gateway-client.ts";

const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; });

/** A fake Gateway behind the relay: the key's scopes decide 403 vs 400/404, as gateway/app.py does. */
function gateway(scopes: string[]) {
  const seen: string[] = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const path = String(input).replace(/^\/api\/gateway/, "");
    seen.push(`${init?.method ?? "GET"} ${path}`);
    const lacks = (s: string) => new Response(JSON.stringify({ error: { code: "forbidden", message: `this key lacks the '${s}' scope` } }), { status: 403 });
    if (path === "/v1/models") return new Response("{}", { status: 200 });
    if (path.startsWith("/v1/judge/")) return scopes.includes("judge") ? new Response("{}", { status: 400 }) : lacks("judge");
    if (path.endsWith("/cancel")) return scopes.includes("jobs:write") ? new Response("{}", { status: 404 }) : lacks("jobs:write");
    if (path.startsWith("/v1/jobs/")) return scopes.includes("jobs:read") ? new Response("{}", { status: 404 }) : lacks("jobs:read");
    return new Response("{}", { status: 404 });
  }) as typeof fetch;
  return seen;
}

test("Connect key checks the judge scope with a request that does no work", async () => {
  const seen = gateway(["jobs:read", "jobs:write"]);
  const r = await checkKey("lorde_sk_x", ["judge"]);
  assert.equal(r.ok, false);
  assert.match((r as { message: string }).message, /lacks the judge scope/);
  assert.deepEqual(seen, ["GET /v1/models", "POST /v1/judge/reward"]);
  gateway(["judge"]);
  assert.deepEqual(await checkKey("lorde_sk_x", ["judge"]), { ok: true });
});

test("Connect key names every missing scope, and a 403 on /v1/models means revoked", async () => {
  gateway(["judge"]);
  const r = await checkKey("lorde_sk_x", ["jobs:read", "jobs:write"]);
  assert.match((r as { message: string }).message, /lacks the jobs:read and jobs:write scopes/);
  globalThis.fetch = (async () => new Response(JSON.stringify({ error: { message: "key revoked" } }), { status: 403 })) as typeof fetch;
  assert.match((await checkKey("lorde_sk_x", ["judge"]) as { message: string }).message, /revoked/);
});
