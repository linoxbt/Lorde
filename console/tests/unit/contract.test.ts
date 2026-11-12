import assert from "node:assert/strict";
import { test } from "node:test";

import { curatorkitAction, parseReply } from "../../lib/contract.ts";

test("strict JSON, one fence allowed, prose rejected", () => {
  assert.equal(parseReply("grounding", '{"grounding_score": 0.1}').ok, true);
  assert.equal(parseReply("reward", '```json\n{"overall_score": 0.8}\n```').ok, true);
  assert.equal(parseReply("reward", 'Sure! {"overall_score": 0.9}').ok, false);
  assert.equal(parseReply("reward", '{"overall_score": 1.2}').ok, false);
});

test("dimension fallback mirrors CuratorKIT", () => {
  const r = parseReply("reward", '{"helpfulness": 1.0, "honesty": 0.5}');
  assert.ok(r.ok && r.usedFallback && r.score === 0.75);
});

test("CuratorKIT's action at the threshold", () => {
  assert.equal(curatorkitAction("grounding", '{"grounding_score": 0.7}').decision, "pass");
  assert.equal(curatorkitAction("grounding", '{"grounding_score": 0.69}').reason, "hallucination_contract_failed");
  assert.equal(curatorkitAction("reward", "I think it is good").reason, "judge_error:JudgeParseError");
});
