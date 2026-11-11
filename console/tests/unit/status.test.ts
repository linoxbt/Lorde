import assert from "node:assert/strict";
import { test } from "node:test";

import { BASE_NOTICE, servedModel, summarize, type Check } from "../../lib/status.ts";

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
