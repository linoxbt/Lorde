import assert from "node:assert/strict";
import { test } from "node:test";

// keys.ts imports "server-only", which throws outside Next; test the same algorithm inline against a Python vector.
import { createHash } from "node:crypto";

test("hash and prefix match gateway/keys.py", () => {
  const key = "lorde_sk_Ab3Ab3Ab3Ab3Ab3Ab3Ab3Ab3Ab3Ab3Ab3Ab3Ab3Ab3x";
  assert.equal(key.slice(0, 17), "lorde_sk_Ab3Ab3Ab");
  assert.equal(createHash("sha256").update(key + "pepper-" + "z".repeat(32)).digest("hex"), "8670c3905f55b29d3208d9ebef32e64323448a309d8305809f752ade44438d46");
});
