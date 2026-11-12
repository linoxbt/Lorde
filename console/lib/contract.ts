/**
 * A TypeScript mirror of CuratorKIT's judge parser (parse_judge_reply + extract_score), used by the
 * playground to show what CuratorKIT would do with a reply. The Python package remains the source of truth.
 */
import type { Gate } from "./types";

export const THRESHOLD = 0.7;
const PRIMARY: Record<Gate, string> = { reward: "overall_score", grounding: "grounding_score" };
const DIMS = ["helpfulness", "honesty", "instruction_following"];

export type Parsed = { ok: true; score: number; parsed: Record<string, unknown>; usedFallback: boolean } |
  { ok: false; error: string };

export function parseJudgeReply(reply: string): unknown {
  let text = reply.trim();
  if (text.startsWith("```") && text.endsWith("```") && text.length >= 6) {
    let inner = text.slice(3, -3);
    if (inner.startsWith("json")) inner = inner.slice(4);
    text = inner.trim();
  }
  try { return JSON.parse(text); } catch { return null; }
}

function unit(v: unknown, key: string): number {
  if (typeof v !== "number" || Number.isNaN(v)) throw new Error(`judge '${key}' is not a number`);
  if (v < 0 || v > 1) throw new Error(`judge '${key}' is outside 0-1`);
  return v;
}

export function parseReply(kind: Gate, reply: string): Parsed {
  const parsed = parseJudgeReply(reply);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return { ok: false, error: "judge reply has no JSON score" };
  const p = parsed as Record<string, unknown>;
  try {
    const dims = kind === "reward" ? DIMS.filter((k) => k in p).map((k) => unit(p[k], k)) : [];
    const key = PRIMARY[kind];
    if (key in p) return { ok: true, score: unit(p[key], key), parsed: p, usedFallback: false };
    if (dims.length) return { ok: true, score: dims.reduce((a, b) => a + b, 0) / dims.length, parsed: p, usedFallback: true };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  return { ok: false, error: "judge reply has no JSON score" };
}

export function curatorkitAction(kind: Gate, reply: string, threshold = THRESHOLD) {
  const r = parseReply(kind, reply);
  if (!r.ok) return { decision: "reject" as const, reason: "judge_error:JudgeParseError", detail: r.error, score: null };
  const pass = r.score >= threshold;
  return {
    decision: pass ? ("pass" as const) : ("reject" as const), score: r.score, detail: "",
    reason: pass ? null : kind === "reward" ? `below_reward_threshold:${r.score.toFixed(2)}` : "hallucination_contract_failed",
  };
}
