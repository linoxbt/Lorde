import "server-only";

import { redirect } from "next/navigation";
import { cache } from "react";

import { demoDatasets, demoJobs, demoJudgments, demoKeys, demoMembers, demoReview, demoUsage, demoWorkspace } from "./demo";
import { supabaseEnabled } from "./env";
import { type Client, DataError, primaryMembership, UUID } from "./membership";
import type {
  ActiveModel, ApiKeyRow, Dataset, Gate, GateRates, HistCell, Job, Judgment, JudgmentFilters, Member, Page, ReviewItem,
  ReviewLabel, Role, UsageDay, Workspace,
} from "./types";

/**
 * Console data. Live mode reads through Supabase with the user's own session, so the RLS policies and the
 * functions in supabase/migrations decide what is visible; aggregates run in SQL (0006_console.sql). Every list is
 * bounded and returns its exact total. Demo mode returns deterministic sample data.
 */
export const ROWS_PER_PAGE = 25;
export const JOBS_PER_PAGE = 25;
const JOB_COLS = "id, status, dataset_id, config, model_revision, counts, error, cancel_requested, created_at, started_at, finished_at, datasets(name)";

type Live = { sb: Client; userId: string; email: string; orgId: string; role: Role };

/** The signed-in user and their organization, once per request. No session → /signin; no organization → /welcome. */
const live = cache(async (): Promise<Live> => {
  const { supabaseServer } = await import("./supabase/server");
  const sb = await supabaseServer();
  const { data: auth } = await sb.auth.getUser();
  if (!auth.user) redirect("/signin?next=/console");
  const m = await primaryMembership(sb, auth.user.id);
  if (!m) redirect("/welcome");
  return { sb, userId: auth.user.id, email: auth.user.email ?? "", orgId: m.org_id, role: m.role };
});

function must<T>(res: { data: T | null; error: { message?: string; code?: string } | null }, what: string): T {
  if (res.error) throw new DataError(what, res.error);
  return res.data as T;
}

const n = (v: unknown) => Number(v ?? 0) || 0;

export const getWorkspace = cache(async (): Promise<Workspace> => {
  if (!supabaseEnabled()) return demoWorkspace();
  const l = await live();
  const [org, model] = await Promise.all([
    l.sb.from("orgs").select("id, name, retention_days, store_content").eq("id", l.orgId).single(),
    l.sb.from("model_versions").select("name, hf_repo, revision, promoted_at, bench").eq("state", "active").maybeSingle(),
  ]);
  const m = must(model, "the active model");
  const activeModel: ActiveModel = m ? { name: m.name, repo: m.hf_repo, revision: m.revision, promoted_at: m.promoted_at, bench: m.bench } : null;
  return { mode: "live", org: must(org, "your organization"), me: { id: l.userId, email: l.email, role: l.role }, activeModel };
});

type JobRow = Omit<Job, "dataset_name"> & { datasets?: { name: string | null } | null };
const toJob = (j: JobRow): Job => {
  const { datasets, ...rest } = j;
  return { ...rest, counts: rest.counts ?? {}, dataset_name: datasets?.name ?? undefined };
};

export async function listJobs(page = 0, opts: { status?: Job["status"]; size?: number } = {}): Promise<Page<Job>> {
  const size = opts.size ?? JOBS_PER_PAGE;
  if (!supabaseEnabled()) {
    const all = demoJobs.filter((j) => !opts.status || j.status === opts.status);
    return { rows: all.slice(page * size, page * size + size), total: all.length };
  }
  const l = await live();
  let q = l.sb.from("jobs").select(JOB_COLS, { count: "exact" }).eq("org_id", l.orgId);
  if (opts.status) q = q.eq("status", opts.status);
  const res = await q.order("created_at", { ascending: false }).order("id").range(page * size, page * size + size - 1);
  return { rows: must(res, "jobs").map((j) => toJob(j as unknown as JobRow)), total: res.count ?? 0 };
}

export async function getJob(id: string): Promise<Job | null> {
  if (!supabaseEnabled()) return demoJobs.find((j) => j.id === id) ?? null;
  if (!UUID.test(id)) return null;
  const l = await live();
  const j = must(await l.sb.from("jobs").select(JOB_COLS).eq("id", id).eq("org_id", l.orgId).maybeSingle(), "the job");
  return j ? toJob(j as unknown as JobRow) : null;
}

export async function listDatasets(limit = 200): Promise<Page<Dataset>> {
  if (!supabaseEnabled()) return { rows: demoDatasets, total: demoDatasets.length };
  const l = await live();
  const res = await l.sb.from("datasets").select("id, name, format, rows, bytes, created_at, expires_at", { count: "exact" })
    .eq("org_id", l.orgId).order("created_at", { ascending: false }).limit(limit);
  return { rows: must(res, "datasets") as Dataset[], total: res.count ?? 0 };
}

export async function listKeys(limit = 200): Promise<Page<ApiKeyRow>> {
  if (!supabaseEnabled()) return { rows: demoKeys, total: demoKeys.length };
  const l = await live();
  const res = await l.sb.from("api_keys").select("id, name, prefix, scopes, rpm, created_at, last_used_at, revoked_at", { count: "exact" })
    .eq("org_id", l.orgId).order("created_at", { ascending: false }).limit(limit);
  return { rows: must(res, "API keys") as ApiKeyRow[], total: res.count ?? 0 };
}

export async function listMembers(): Promise<Member[]> {
  if (!supabaseEnabled()) return demoMembers;
  const l = await live();
  const rows = must(await l.sb.rpc("org_members_with_email", { o: l.orgId }), "members") as Member[];
  return rows ?? [];
}

/** Usage per UTC day, oldest first, every day present (zero when idle). */
export async function usageDaily(days = 14): Promise<UsageDay[]> {
  if (!supabaseEnabled()) return demoUsage(days);
  const l = await live();
  const rows = must(await l.sb.rpc("usage_daily", { o: l.orgId, days }), "usage") as Record<string, unknown>[];
  return (rows ?? []).map((r) => ({ day: String(r.day).slice(0, 10), judgments: n(r.judgments), calls: n(r.calls),
    prompt_tokens: n(r.prompt_tokens), completion_tokens: n(r.completion_tokens) }));
}

/** Pass rate per gate over the judgments of jobs from the last `days` days. */
export async function gatePassRates(days = 30): Promise<GateRates> {
  const out: GateRates = { grounding: { judged: 0, passed: 0 }, reward: { judged: 0, passed: 0 } };
  if (!supabaseEnabled()) {
    for (const j of demoJobs.filter((x) => x.status === "succeeded")) {
      for (const r of demoJudgments(j.id)) {
        if (r.decision === null) continue;
        out[r.gate].judged++;
        if (r.decision) out[r.gate].passed++;
      }
    }
    return out;
  }
  const l = await live();
  const rows = must(await l.sb.rpc("gate_pass_rates", { o: l.orgId, days }), "pass rates") as Record<string, unknown>[];
  for (const r of rows ?? []) out[r.gate as Gate] = { judged: n(r.judged), passed: n(r.passed) };
  return out;
}

export async function jobStats(jobId: string): Promise<HistCell[]> {
  if (!supabaseEnabled()) {
    const cells = new Map<string, HistCell>();
    for (const r of demoJudgments(jobId)) {
      const bin = r.score === null ? null : Math.min(9, Math.floor(r.score * 10));
      const k = `${r.gate}|${r.lang ?? "?"}|${bin}`;
      const c = cells.get(k) ?? { gate: r.gate, lang: r.lang ?? "?", bin, passed: 0, rejected: 0, undecided: 0 };
      if (r.decision === null) c.undecided++; else if (r.decision) c.passed++; else c.rejected++;
      cells.set(k, c);
    }
    return [...cells.values()];
  }
  const l = await live();
  const rows = must(await l.sb.rpc("job_judgment_stats", { job: jobId }), "the score histogram") as Record<string, unknown>[];
  return (rows ?? []).map((r) => ({ gate: r.gate as Gate, lang: String(r.lang ?? "?"), bin: r.bin === null ? null : n(r.bin),
    passed: n(r.passed), rejected: n(r.rejected), undecided: n(r.undecided) }));
}

/** One page of a job's judgments, filtered in SQL, with the exact filtered total. */
export async function judgmentsPage(jobId: string, f: JudgmentFilters): Promise<Page<Judgment>> {
  const from = f.page * ROWS_PER_PAGE;
  if (!supabaseEnabled()) {
    const all = demoJudgments(jobId).filter((r) => (!f.gate || r.gate === f.gate)
      && (!f.decision || (f.decision === "none" ? r.decision === null : r.decision === (f.decision === "pass")))
      && (!f.lang || (f.lang === "?" ? r.lang === null : r.lang === f.lang)));
    return { rows: all.slice(from, from + ROWS_PER_PAGE), total: all.length };
  }
  const l = await live();
  let q = l.sb.from("judgments").select("row_idx, gate, lang, score, decision, verdict", { count: "exact" }).eq("job_id", jobId);
  if (f.gate) q = q.eq("gate", f.gate);
  if (f.decision === "none") q = q.is("decision", null);
  else if (f.decision) q = q.eq("decision", f.decision === "pass");
  if (f.lang === "?") q = q.is("lang", null);
  else if (f.lang) q = q.eq("lang", f.lang);
  const res = await q.order("row_idx").order("gate").range(from, from + ROWS_PER_PAGE - 1);
  return { rows: must(res, "judgments") as Judgment[], total: res.count ?? 0 };
}

/**
 * Up to 200 judged rows (both gates) for the New job preview: from the newest succeeded job on `datasetId` when
 * there is one, else from the newest succeeded job overall. `sameDataset` says which.
 */
export async function previewSample(datasetId?: string): Promise<{ sample: Pick<Judgment, "gate" | "score">[]; from: Job | null; sameDataset: boolean }> {
  const LIMIT = 400;
  if (!supabaseEnabled()) {
    const done = demoJobs.filter((j) => j.status === "succeeded");
    const job = done.find((j) => j.dataset_id === datasetId) ?? done[0] ?? null;
    return { sample: job ? demoJudgments(job.id).slice(0, LIMIT) : [], from: job, sameDataset: Boolean(job && job.dataset_id === datasetId) };
  }
  const l = await live();
  const newest = async (ds?: string) => {
    let q = l.sb.from("jobs").select(JOB_COLS).eq("org_id", l.orgId).eq("status", "succeeded");
    if (ds) q = q.eq("dataset_id", ds);
    const j = must(await q.order("created_at", { ascending: false }).limit(1).maybeSingle(), "recent jobs");
    return j ? toJob(j as unknown as JobRow) : null;
  };
  const own = datasetId && UUID.test(datasetId) ? await newest(datasetId) : null;
  const job = own ?? await newest();
  if (!job) return { sample: [], from: null, sameDataset: false };
  const rows = must(await l.sb.from("judgments").select("gate, score").eq("job_id", job.id).order("row_idx").limit(LIMIT), "the preview sample");
  return { sample: rows as Pick<Judgment, "gate" | "score">[], from: job, sameDataset: Boolean(own) };
}

const TEXT_FIELDS = ["instruction", "input", "output", "response", "source_text", "question", "answer"] as const;

/** Review queue (rows this user has not labelled yet, from jobs that stored content) and this user's labels. */
export async function reviewData(): Promise<{ items: ReviewItem[]; labels: ReviewLabel[] }> {
  if (!supabaseEnabled()) return { items: demoReview, labels: [] };
  const l = await live();
  const [queue, mine] = await Promise.all([
    l.sb.rpc("review_queue", { o: l.orgId, n: 50 }),
    l.sb.rpc("my_review_labels", { o: l.orgId, n: 500 }),
  ]);
  const items: ReviewItem[] = [];
  for (const r of (must(queue, "the review queue") ?? []) as Record<string, unknown>[]) {
    if (r.score === null || r.score === undefined) continue;  // nothing to compare against
    const parsed = (r.parsed ?? {}) as Record<string, unknown>;
    const text = Object.fromEntries(TEXT_FIELDS.filter((k) => typeof parsed[k] === "string").map((k) => [k, parsed[k] as string]));
    items.push({ ref: `judgment:${r.id}`, kind: r.gate as Gate, lang: String(r.lang ?? parsed.lang ?? "?"), lorde_score: n(r.score),
      job_id: String(r.job_id), row_idx: n(r.row_idx), ...text });
  }
  const labels = ((must(mine, "your labels") ?? []) as Record<string, unknown>[]).map((r) => ({
    ref: `judgment:${r.judgment_id}`, kind: r.kind as Gate, human: n(r.human_score), lorde: n(r.lorde_score) }));
  return { items, labels };
}
