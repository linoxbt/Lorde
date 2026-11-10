/**
 * Demo-mode judge: a transparent heuristic, clearly labelled "simulated" in the UI. It produces replies in
 * the gate's JSON shape so the playground can show CuratorKIT's behaviour without a model. Not Lorde.
 */
import type { Gate } from "./types";

const STOP = new Set("the a an of to in and or is are was were be it its on for with as by at from that this which who what how when where why".split(" "));
const words = (s: string) => s.toLowerCase().normalize("NFKC").match(/[\p{L}\p{N}][\p{L}\p{N}'.,-]*/gu)?.map((w) => w.replace(/[.,]+$/, "")) ?? [];
const content = (s: string) => words(s).filter((w) => !STOP.has(w) && w.length > 2);
const nums = (s: string) => s.match(/\d[\d.,]*/g)?.map((n) => n.replace(/[.,]$/, "")) ?? [];
const hash = (s: string) => [...s].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7);
const r2 = (x: number) => Math.round(Math.min(1, Math.max(0, x)) * 100) / 100;

export function demoGrounding(source: string, answer: string) {
  const src = new Set(content(source));
  const srcNums = new Set(nums(source).map((n) => n.replace(/[.,]/g, "")));
  const claims = answer.split(/(?<=[.!?।؟])\s+/).map((c) => c.trim()).filter(Boolean);
  const supported: string[] = [], unsupported: string[] = [];
  for (const c of claims) {
    const cw = content(c);
    const overlap = cw.length ? cw.filter((w) => src.has(w)).length / cw.length : 0;
    const badNum = nums(c).some((n) => !srcNums.has(n.replace(/[.,]/g, "")));
    (overlap >= 0.6 && !badNum ? supported : unsupported).push(c.slice(0, 140));
  }
  const total = Math.max(1, claims.length);
  const score = r2(claims.length ? (supported.length / total) * 0.95 + (unsupported.length ? 0 : 0.05) : 0);
  const verdict = score >= 0.7 ? "grounded" : score >= 0.4 ? "partially_grounded" : "hallucinated";
  return { grounding_score: score, supported_claims: supported.slice(0, 4), unsupported_claims: unsupported.slice(0, 4), verdict };
}

export function demoReward(instruction: string, response: string) {
  const ins = content(instruction), res = content(response);
  const coverage = ins.length ? ins.filter((w) => res.includes(w)).length / ins.length : 0.5;
  const len = response.trim().length;
  const lengthOk = len < 15 ? 0.15 : len < 40 ? 0.45 : len > 4000 ? 0.6 : 0.9;
  const finished = /[.!?।؟)"”]$/.test(response.trim()) ? 1 : 0.7;
  const uniq = res.length ? new Set(res).size / res.length : 1;
  const repetition = uniq < 0.5 ? 0.6 : 1;
  const helpfulness = r2(lengthOk * finished * repetition * (0.6 + 0.4 * Math.min(1, coverage * 1.5)));
  const instruction_following = r2((0.5 + 0.5 * Math.min(1, coverage * 1.6)) * finished * (len < 15 ? 0.4 : 1));
  const honesty = r2(0.85 * repetition);
  const overall = r2(0.4 * helpfulness + 0.35 * instruction_following + 0.25 * honesty);
  return {
    overall_score: overall, dimension_scores: { helpfulness, honesty, instruction_following },
    strengths: overall >= 0.7 ? "Addresses the instruction directly and completely." : len >= 40 ? "Some relevant content." : "",
    weaknesses: overall >= 0.7 ? "Minor: could be more specific." : len < 40 ? "Too short to be useful." : "Misses parts of the instruction.",
  };
}

/** Simulated replies: Lorde (strict JSON) and an untuned small model that sometimes wraps JSON in prose. */
export function simulate(kind: Gate, f: Record<string, string>) {
  const body = kind === "grounding" ? demoGrounding(f.source_text ?? "", f.answer ?? "") : demoReward(f.instruction ?? "", f.response ?? "");
  const lorde = JSON.stringify(body);
  const h = hash(JSON.stringify(f));
  const base = h % 3 === 0 ? lorde.replace(/0\.\d+/, (m) => String(Math.min(1, Number(m) + 0.1).toFixed(2)))
    : h % 3 === 1 ? `Here is my evaluation of the answer:\n${JSON.stringify(body, null, 2)}\nLet me know if you need more detail.`
    : `${JSON.stringify(body).slice(0, -12)}`;
  return { lorde, base };
}
