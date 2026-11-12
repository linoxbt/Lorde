export type Gate = "reward" | "grounding";
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type Role = "owner" | "admin" | "member";

export type Org = { id: string; name: string; retention_days: number; store_content: boolean };
export type Member = { user_id: string; email: string; role: Role; created_at: string };
export type Dataset = {
  id: string; name: string; format: "jsonl" | "csv" | "parquet"; rows: number | null; bytes: number | null;
  created_at: string; expires_at: string | null;
};
export type JobCounts = {
  rows_in?: number; evaluated?: number; passed?: number; rejected?: number; pass_rate?: number | null;
  rejection_reasons?: Record<string, number>; judge_errors?: number; progress?: string; wall_s?: number;
  notice?: string; not_evaluated?: number; quota_rows?: number;
};
export type Job = {
  id: string; status: JobStatus; dataset_id: string | null; dataset_name?: string;
  config: {
    gates: { grounding: number | null; reward: number | null }; options: { pii: boolean; dedup: string };
    /** What the Gateway recorded: an upload's storage path, or a Hub dataset URL (hf://datasets/...). */
    source?: { storage_path?: string; format?: string; dataset_url?: string | null };
  };
  model_revision: string | null; counts: JobCounts; error: string | null; cancel_requested?: boolean;
  created_at: string; started_at: string | null; finished_at: string | null;
};
export type Judgment = {
  row_idx: number; gate: Gate; lang: string | null; score: number | null; decision: boolean | null; verdict: string | null;
  reason?: string; preview?: string;
};
export type ApiKeyRow = {
  id: string; name: string; prefix: string; scopes: string[]; rpm: number; created_at: string;
  last_used_at: string | null; revoked_at: string | null;
};
export type UsageDay = { day: string; judgments: number; calls: number; prompt_tokens: number; completion_tokens: number };
/** One judged row to label. `ref` is what human_labels.item_ref stores ("judgment:<id>" in live mode). */
export type ReviewItem = {
  ref: string; kind: Gate; lang: string; instruction?: string; input?: string; output?: string; response?: string;
  source_text?: string; question?: string; answer?: string; lorde_score: number;
  job_id?: string; row_idx?: number;
};
export type ReviewLabel = { ref: string; kind: Gate; human: number; lorde: number };
/** A page of rows plus the exact total, so screens can say "showing N of M". */
export type Page<T> = { rows: T[]; total: number };
/** Histogram cell from job_judgment_stats(): bin is the 0.1-wide score bucket, null when unscored. */
export type HistCell = { gate: Gate; lang: string; bin: number | null; passed: number; rejected: number; undecided: number };
export type GateRates = Record<Gate, { judged: number; passed: number }>;
export type ActiveModel = {
  name: string | null; repo: string | null; revision: string; promoted_at: string | null;
  bench: { acceptance?: { criterion: string; value: string; met: boolean | null; blocking?: boolean }[] } | null;
} | null;
export type Workspace = {
  mode: "demo" | "live"; org: Org; me: { id: string; email: string; role: Role }; activeModel: ActiveModel;
};
export type JudgmentFilters = { gate?: Gate; decision?: "pass" | "reject" | "none"; lang?: string; page: number };
