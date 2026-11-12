import assert from "node:assert/strict";
import { test } from "node:test";

import { isPastEnd, isRangePastEnd, MAX_PAGE, pageCount, parsePage } from "../../lib/paging.ts";

test("parsePage: 1-based query value to a zero-based, bounded index", () => {
  assert.equal(parsePage(undefined), 0);
  assert.equal(parsePage("1"), 0);
  assert.equal(parsePage("3"), 2);
  assert.equal(parsePage(["4", "9"]), 3);
  for (const junk of ["0", "-5", "abc", "", "NaN"]) assert.equal(parsePage(junk), 0, junk);
  assert.equal(parsePage("999999999"), MAX_PAGE - 1);
});

test("a page past the end is recognised, both from PostgREST and from the total", () => {
  assert.equal(isRangePastEnd({ code: "PGRST103", status: 416 }), true);
  assert.equal(isRangePastEnd({ status: 416 }), true);
  assert.equal(isRangePastEnd({ code: "42501" }), false);
  assert.equal(isRangePastEnd(null), false);
  assert.equal(pageCount(0, 25), 1);
  assert.equal(pageCount(51, 25), 3);
  assert.equal(isPastEnd(0, 0, 25), false);  // an empty list is "no jobs yet", not past the end
  assert.equal(isPastEnd(2, 51, 25), false);
  assert.equal(isPastEnd(3, 51, 25), true);
  assert.equal(isPastEnd(998, 30, 25), true);
});
