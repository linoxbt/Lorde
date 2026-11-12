import assert from "node:assert/strict";
import { test } from "node:test";

import { nextUnlabelled } from "../../lib/review.ts";

test("the review queue never re-offers a labelled row", () => {
  const refs = ["a", "b", "c", "d"];
  assert.equal(nextUnlabelled(refs, new Set(), 0), 1);
  assert.equal(nextUnlabelled(refs, new Set(["b"]), 0), 2);
  // at the end, wrap round to the skipped rows instead of staying on the last (already labelled) one
  assert.equal(nextUnlabelled(refs, new Set(["d"]), 3), 0);
  assert.equal(nextUnlabelled(refs, new Set(["a", "b", "d"]), 3), 2);
  // the current row counts too: labelling the only unlabelled row leaves nothing
  assert.equal(nextUnlabelled(refs, new Set(refs), 1), null);
  assert.equal(nextUnlabelled(["a"], new Set(), 0), 0, "a skipped single row is offered again");
  assert.equal(nextUnlabelled([], new Set(), 0), null);
});
