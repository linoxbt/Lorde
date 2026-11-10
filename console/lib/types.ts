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
};
export type Job = {
  id: string; status: JobStatus; dataset_id: string | null; dataset_name?: string;
  config: { gates: { grounding: number | null; reward: number | null }; options: { pii: boolean; dedup: string } };
  model_revision: string | null; counts: JobCounts; error: string | null;
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
export type ReviewItem = {
  ref: string; kind: Gate; lang: string; instruction?: string; response?: string; source_text?: string; question?: string;
  answer?: string; lorde_score: number;
};
export type Workspace = {
  mode: "demo" | "live"; org: Org; me: { email: string; role: Role };
  datasets: Dataset[]; jobs: Job[]; keys: ApiKeyRow[]; usage: UsageDay[]; members: Member[];
  activeModel: { revision: string; repo: string };
};
