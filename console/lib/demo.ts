/** Deterministic demo workspace: the Console runs fully without Supabase, clearly labelled as demo data. */
import type { ApiKeyRow, Dataset, Job, Judgment, Member, ReviewItem, UsageDay, Workspace } from "./types";

function rng(seed: number) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const NOW = Date.parse("2026-11-10T09:00:00Z");
/** The demo workspace's own "now": its datasets never expire, whatever the real date. */
export const DEMO_NOW = NOW;
const ago = (h: number) => new Date(NOW - h * 3600_000).toISOString();
export const LANGS = ["en", "hi", "fr", "ar", "es", "pt", "id", "tr"] as const;

export const demoDatasets: Dataset[] = [
  { id: "d1", name: "support-answers-v3.jsonl", format: "jsonl", rows: 4820, bytes: 9_431_200, created_at: ago(30), expires_at: ago(-690) },
  { id: "d2", name: "policy-qa-multilingual.parquet", format: "parquet", rows: 12_400, bytes: 31_200_000, created_at: ago(54), expires_at: ago(-666) },
  { id: "d3", name: "synthetic-rag-batch-07.jsonl", format: "jsonl", rows: 2210, bytes: 4_120_000, created_at: ago(78), expires_at: ago(-642) },
  { id: "d4", name: "agent-traces-sample.csv", format: "csv", rows: 860, bytes: 1_904_000, created_at: ago(140), expires_at: ago(-580) },
];

function job(id: string, ds: Dataset, status: Job["status"], h: number, passRate: number, gates: Job["config"]["gates"]): Job {
  const n = ds.rows ?? 1000;
  const done = status === "succeeded";
  const passed = Math.round(n * passRate);
  const rej = n - passed;
  return {
    id, status, dataset_id: ds.id, dataset_name: ds.name, model_revision: "lorde-tiny-aya@v1.0.0",
    config: { gates, options: { pii: id === "j2", dedup: "exact" } },
    counts: done ? {
      rows_in: n, evaluated: n, passed, rejected: rej, pass_rate: passRate, judge_errors: 0, wall_s: Math.round(n * 0.11),
      rejection_reasons: { hallucination_contract_failed: Math.round(rej * 0.58), below_reward_threshold: Math.round(rej * 0.37),
        schema: rej - Math.round(rej * 0.58) - Math.round(rej * 0.37) },
    } : { progress: status === "running" ? "running" : status },
    error: status === "failed" ? "job process exited 1: dataset has no instruction/output columns" : null,
    created_at: ago(h), started_at: status === "queued" ? null : ago(h - 0.05),
    finished_at: done || status === "failed" || status === "cancelled" ? ago(h - 0.4) : null,
  };
}

export const demoJobs: Job[] = [
  job("7f3c9a12-0b1e-4c2d-9a51-3e8d2f6b1a01", demoDatasets[0]!, "running", 0.3, 0, { grounding: 0.7, reward: 0.7 }),
  job("2b8e4d70-5a9c-4f13-8e62-1d7c3b9a0f02", demoDatasets[1]!, "succeeded", 26, 0.712, { grounding: 0.7, reward: 0.7 }),
  job("c41a6e98-3d27-4b85-a0f4-6e2b8c1d5a03", demoDatasets[2]!, "succeeded", 50, 0.634, { grounding: 0.75, reward: null }),
  job("9d05b3f1-8e64-4a2c-b7d9-0f3a6c2e8b04", demoDatasets[3]!, "failed", 120, 0, { grounding: null, reward: 0.7 }),
  job("e6a7c2d4-1f58-4b93-9c06-7d8e4a3b2c05", demoDatasets[2]!, "cancelled", 74, 0, { grounding: 0.7, reward: 0.7 }),
];

export function demoJudgments(jobId: string, n = 240): Judgment[] {
  const r = rng(jobId.split("").reduce((a, c) => a + c.charCodeAt(0), 7));
  const reasons = ["hallucination_contract_failed", "below_reward_threshold"];
  const out: Judgment[] = [];
  for (let i = 0; i < n; i++) {
    const lang = LANGS[Math.floor(r() * LANGS.length)]!;
    const gate = r() < 0.55 ? "grounding" : "reward";
    const good = r() < 0.7;
    const score = Math.round((good ? 0.7 + r() * 0.3 : r() * 0.68) * 100) / 100;
    const verdict = gate === "grounding" ? (score >= 0.7 ? "grounded" : score >= 0.4 ? "partially_grounded" : "hallucinated") : null;
    out.push({ row_idx: i, gate, lang, score, decision: score >= 0.7, verdict,
      reason: score >= 0.7 ? undefined : reasons[gate === "grounding" ? 0 : 1],
      preview: SAMPLE_PREVIEWS[i % SAMPLE_PREVIEWS.length] });
  }
  return out;
}

const SAMPLE_PREVIEWS = [
  "How long is the Niger River? → It is about 4,180 km long.",
  "Summarize the refund policy → Refunds within 30 days with a receipt.",
  "Quelle est la capitale du Sénégal ? → Dakar est la capitale.",
  "¿Cuándo abre la tienda? → Abre a las 9:00 de lunes a sábado.",
  "What does clause 4.2 require? → Notice in writing within 14 days.",
  "Explain photosynthesis briefly → Plants convert light into chemical energy.",
];

export const demoKeys: ApiKeyRow[] = [
  { id: "k1", name: "ci-pipeline", prefix: "lorde_sk_Q7dK2mPa", scopes: ["judge", "jobs:read", "jobs:write"], rpm: 600, created_at: ago(400), last_used_at: ago(0.2), revoked_at: null },
  { id: "k2", name: "notebook", prefix: "lorde_sk_vX81nBe4", scopes: ["judge"], rpm: 120, created_at: ago(200), last_used_at: ago(20), revoked_at: null },
  { id: "k3", name: "old-staging", prefix: "lorde_sk_3HhLq0Zs", scopes: ["judge"], rpm: 600, created_at: ago(900), last_used_at: ago(700), revoked_at: ago(600) },
];

export const demoMembers: Member[] = [
  { user_id: "u1", email: "lino@lorde.dev", role: "owner", created_at: ago(900) },
  { user_id: "u2", email: "ada@lorde.dev", role: "admin", created_at: ago(600) },
  { user_id: "u3", email: "kofi@lorde.dev", role: "member", created_at: ago(300) },
];

export function demoUsage(days = 14): UsageDay[] {
  const r = rng(42);
  return Array.from({ length: days }, (_, i) => {
    const d = new Date(NOW - (days - 1 - i) * 86400_000);
    const j = Math.round(8000 + r() * 9000 + i * 900);
    return { day: d.toISOString().slice(0, 10), judgments: j, calls: Math.round(j * 0.62),
      prompt_tokens: j * 720, completion_tokens: j * 96 };
  });
}

export const demoReview: ReviewItem[] = [
  { ref: "rv-1", kind: "grounding", lang: "en", source_text: "The Niger River is the main river of West Africa, extending about 4,180 km. Its drainage basin is 2,117,700 km² in area.", question: "How long is the Niger River?", answer: "It is about 6,650 km long.", lorde_score: 0.1 },
  { ref: "rv-2", kind: "reward", lang: "fr", instruction: "Explique en deux phrases pourquoi le ciel est bleu.", response: "La lumière du soleil est diffusée par les molécules de l'air, et les courtes longueurs d'onde bleues sont les plus diffusées. C'est pourquoi le ciel nous paraît bleu.", lorde_score: 0.86 },
  { ref: "rv-3", kind: "grounding", lang: "es", source_text: "La tienda abre de lunes a sábado de 9:00 a 20:00. Los domingos permanece cerrada.", question: "¿Abre la tienda los domingos?", answer: "No, los domingos permanece cerrada.", lorde_score: 0.93 },
  { ref: "rv-4", kind: "reward", lang: "en", instruction: "List three safety checks before deploying a model.", response: "Test it.", lorde_score: 0.22 },
  { ref: "rv-5", kind: "grounding", lang: "id", source_text: "Candi Borobudur dibangun pada abad ke-9 pada masa Dinasti Syailendra di Jawa Tengah.", question: "Kapan Borobudur dibangun?", answer: "Borobudur dibangun pada abad ke-9.", lorde_score: 0.91 },
  { ref: "rv-6", kind: "grounding", lang: "tr", source_text: "Galata Kulesi, 1348 yılında Cenevizliler tarafından inşa edildi.", question: "Galata Kulesi ne zaman inşa edildi?", answer: "1453 yılında Osmanlılar tarafından inşa edildi.", lorde_score: 0.12 },
];

export function demoWorkspace(): Workspace {
  return {
    mode: "demo", org: { id: "org-demo", name: "Demo workspace", retention_days: 30, store_content: false },
    me: { id: "u1", email: "you@example.com", role: "owner" },
    activeModel: { name: "Lorde (demo)", repo: "lorde-tiny-aya", revision: "v1.0.0", promoted_at: ago(400), bench: null },
  };
}
