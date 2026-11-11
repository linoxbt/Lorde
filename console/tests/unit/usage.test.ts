import assert from "node:assert/strict";
import { test } from "node:test";

import { compact, weekTotals } from "../../lib/usage.ts";

const day = (d: string, judgments: number) => ({ day: d, judgments, calls: 0, prompt_tokens: 0, completion_tokens: 0 });

test("this week is the last 7 calendar days, last week the 7 before", () => {
  const days = [day("2026-10-28", 1000), day("2026-10-29", 1), day("2026-11-04", 2), day("2026-11-05", 4), day("2026-11-11", 8)];
  assert.deepEqual(weekTotals(days, "2026-11-11"), { week: 12, prev: 3 });
  assert.deepEqual(weekTotals(days), { week: 12, prev: 3 });  // defaults to the series' last day
  assert.deepEqual(weekTotals([]), { week: 0, prev: 0 });
});

test("axis labels stay readable for small and large values", () => {
  assert.deepEqual([0, 3, 40, 950, 1200, 12_000, 1_500_000].map(compact), ["0", "3", "40", "950", "1.2k", "12k", "1.5M"]);
});
