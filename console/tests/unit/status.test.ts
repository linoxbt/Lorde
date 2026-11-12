import assert from "node:assert/strict";
import { test } from "node:test";

import { BASE_NOTICE, gatewayChecks, memoize, servedModel, summarize, type Check } from "../../lib/status.ts";

const gw = (state: Check["state"]): Check => ({ name: "Gateway", state, detail: "" });
const serve = (state: Check["state"]): Check => ({ name: "Lorde Serve", state, detail: "" });

test("the summary says only what was checked", () => {
  assert.deepEqual(summarize([gw("ok"), serve("ok")]), { state: "ok", label: "Gateway and model server responding" });
  assert.deepEqual(summarize([gw("ok"), serve("degraded")]),
    { state: "degraded", label: "Gateway responding; model server not ready" });
  assert.deepEqual(summarize([gw("down"), serve("degraded")]), { state: "down", label: "Gateway unreachable" });
  assert.deepEqual(summarize([gw("demo"), serve("demo")]), { state: "demo", label: "Demo mode" });
  for (const checks of [[gw("ok"), serve("ok")], [gw("ok"), serve("degraded")], [gw("down")]]) {
    assert.doesNotMatch(summarize(checks).label, /all systems|operational/i);
  }
});

test("the served model comes from the Gateway when it reports one, else from the configured revision", () => {
  const fromReady = servedModel([{ status: "ready", serve: true }, { model: "lorde-tiny-aya@v1.0.0" }], "ignored");
  assert.deepEqual(fromReady, { label: "lorde-tiny-aya@v1.0.0", revision: "v1.0.0", source: "gateway", base: false });
  assert.equal(servedModel([{ model: { repo: "u/lorde-tiny-aya", revision: "v1.0.1" } }])?.label, "lorde-tiny-aya@v1.0.1");
  assert.equal(servedModel([{ model_repo: "u/lorde-tiny-aya", model_revision: "abc123" }])?.revision, "abc123");
  // /readyz and /healthz today report neither: fall back to NEXT_PUBLIC_LORDE_MODEL_REVISION, or show nothing
  const plain = [{ status: "ready", serve: true, database: true }, { status: "ok", version: "1.0.0" }];
  assert.deepEqual(servedModel(plain, "v1.0.0"), { label: "v1.0.0", revision: "v1.0.0", source: "config", base: false });
  assert.equal(servedModel(plain, ""), null);
  assert.equal(servedModel([null, "x"], undefined), null);
});

test("a base or staging-base revision carries the untrained-base notice", () => {
  const staging = servedModel([{ model: "tiny-aya-global-q4km@staging-base-cpu" }]);
  assert.equal(staging?.base, true);
  assert.equal(staging?.notice, BASE_NOTICE);
  assert.match(BASE_NOTICE, /serving the untrained base model \(staging\)/i);
  assert.equal(servedModel([], "base")?.base, true);
  assert.equal(servedModel([], "lorde-tiny-aya@v1.0.0")?.notice, undefined);
});

const ans = (status: number | null, body: unknown = null) => ({ ok: status !== null && status < 400, status, body, ms: 12 });

test("a check that got no answer is Unknown, not Degraded", () => {
  const ready = { status: "ready", serve: true, database: true, redis: null, breaker_open: false };
  const ok = gatewayChecks(ans(200, { status: "ok" }), ans(200, ready));
  assert.deepEqual(ok.map((c) => [c.name, c.state]), [["Gateway", "ok"], ["Lorde Serve", "ok"], ["Database", "ok"]]);
  assert.equal(summarize(ok).state, "ok");
  // /readyz timed out: nothing is known about the model server
  const timedOut = gatewayChecks(ans(200, { status: "ok" }), ans(null));
  assert.equal(timedOut[1]!.state, "unknown");
  assert.deepEqual(summarize(timedOut), { state: "unknown", label: "Gateway responding; model server status unknown" });
  // an unreadable reply is no answer either
  assert.equal(gatewayChecks(ans(200), ans(502, null))[1]!.state, "unknown");
  // a real "not ready" answer is Degraded, and names the cause
  const notReady = gatewayChecks(ans(200), ans(503, { ...ready, status: "not_ready", serve: false }));
  assert.equal(notReady[1]!.state, "degraded");
  assert.equal(summarize(notReady).label, "Gateway responding; model server not ready");
  const breaker = gatewayChecks(ans(200), ans(503, { ...ready, status: "not_ready", breaker_open: true }));
  assert.match(breaker[1]!.detail, /Circuit open/);
  const db = gatewayChecks(ans(200), ans(503, { ...ready, status: "not_ready", database: false }));
  assert.deepEqual(db.map((c) => c.state), ["ok", "ok", "down"]);
  assert.equal(summarize(db).label, "Gateway responding; database not ready");
  // Gateway unreachable: the model server is not "degraded", it is unknown
  const down = gatewayChecks(ans(null), ans(null));
  assert.deepEqual(down.map((c) => c.state), ["down", "unknown"]);
  assert.equal(summarize(down).label, "Gateway unreachable");
  for (const c of [ok, timedOut, notReady, db, down]) assert.ok(!c.some((x) => /queue|worker/i.test(x.name)), "the job worker is not probed");
});

test("status probes are shared for the TTL", async () => {
  let t = 0, calls = 0;
  const m = memoize(async () => ++calls, 10_000, () => t);
  assert.equal(await m(), 1);
  t = 9_999;
  assert.equal(await m(), 1);
  t = 10_000;
  assert.equal(await m(), 2);
  let fail = true;
  const f = memoize(async () => { if (fail) throw new Error("x"); return "ok"; }, 10_000, () => 0);
  await assert.rejects(f());
  fail = false;
  assert.equal(await f(), "ok", "a failure is not cached");
});
