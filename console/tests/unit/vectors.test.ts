import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { parseReply } from "../../lib/contract.ts";

type V = { kind: "reward" | "grounding"; reply: string; ok: boolean; score: number | null };
const vectors: V[] = JSON.parse(readFileSync(new URL("./curatorkit_vectors.json", import.meta.url), "utf8"));

test("TypeScript parser agrees with CuratorKIT on every recorded vector", () => {
  for (const v of vectors) {
    const r = parseReply(v.kind, v.reply);
    assert.equal(r.ok, v.ok, `${v.kind} ${JSON.stringify(v.reply)}`);
    if (r.ok && v.score !== null) assert.ok(Math.abs(r.score - v.score) < 1e-9, `${v.kind} ${v.reply}`);
  }
});
