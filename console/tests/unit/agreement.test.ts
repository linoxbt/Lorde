import assert from "node:assert/strict";
import { test } from "node:test";

import { agreement } from "../../lib/agreement.ts";

test("kappa matches the Python metrics on the same known example", () => {
  // 20 both pass, 15 both reject, 5 human-only pass, 10 lorde-only pass -> po 0.7, pe 0.5, kappa 0.4
  const pairs = [
    ...Array(20).fill({ human: 0.9, lorde: 0.9 }), ...Array(15).fill({ human: 0.1, lorde: 0.1 }),
    ...Array(5).fill({ human: 0.9, lorde: 0.1 }), ...Array(10).fill({ human: 0.1, lorde: 0.9 }),
  ];
  const r = agreement(pairs);
  assert.equal(r.agreement, 0.7);
  assert.ok(Math.abs((r.kappa ?? 0) - 0.4) < 1e-12);
});
