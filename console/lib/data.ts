import "server-only";

import { demoJudgments, demoWorkspace } from "./demo";
import { supabaseEnabled } from "./env";
import type { ApiKeyRow, Dataset, Job, Judgment, Member, UsageDay, Workspace } from "./types";

/**
 * The signed-in user's workspace. Live mode reads through Supabase with the user's own session, so the
 * RLS policies in supabase/migrations decide what is visible. Demo mode returns deterministic sample data.
 */
export async function getWorkspace(): Promise<Workspace> {
  if (!supabaseEnabled()) return demoWorkspace();
  const { supabaseServer } = await import("./supabase/server");
  const sb = await supabaseServer();
  const { data: auth } = await sb.auth.getUser();
  const user = auth.user;
  const { data: membership } = await sb.from("org_members").select("org_id, role").eq("user_id", user?.id ?? "").limit(1).single();
  const orgId = membership?.org_id as string;
  const since = new Date(Date.now() - 14 * 86400_000).toISOString();
  const [org, datasets, jobs, keys, usage, members] = await Promise.all([
    sb.from("orgs").select("id, name, retention_days, store_content").eq("id", orgId).single(),
    sb.from("datasets").select("id, name, format, rows, bytes, created_at, expires_at").eq("org_id", orgId)
      .order("created_at", { ascending: false }).limit(200),
    sb.from("jobs").select("id, status, dataset_id, config, model_revision, counts, error, created_at, started_at, finished_at")
      .eq("org_id", orgId).order("created_at", { ascending: false }).limit(200),
    sb.from("api_keys").select("id, name, prefix, scopes, rpm, created_at, last_used_at, revoked_at").eq("org_id", orgId)
      .order("created_at", { ascending: false }),
    sb.from("usage_events").select("created_at, judgments, prompt_tokens, completion_tokens").eq("org_id", orgId)
      .gte("created_at", since).limit(50_000),
    sb.from("org_members").select("user_id, role, created_at").eq("org_id", orgId),
  ]);
  const byDay = new Map<string, UsageDay>();
  for (const u of usage.data ?? []) {
    const day = String(u.created_at).slice(0, 10);
    const d = byDay.get(day) ?? { day, judgments: 0, calls: 0, prompt_tokens: 0, completion_tokens: 0 };
    d.judgments += u.judgments ?? 0; d.calls += 1;
    d.prompt_tokens += u.prompt_tokens ?? 0; d.completion_tokens += u.completion_tokens ?? 0;
    byDay.set(day, d);
  }
  const names = new Map((datasets.data ?? []).map((d) => [d.id, d.name]));
  const { data: active } = await sb.from("model_versions").select("revision, hf_repo").eq("state", "active").maybeSingle();
  return {
    mode: "live",
    org: org.data as Workspace["org"],
    me: { email: user?.email ?? "", role: (membership?.role ?? "member") as Workspace["me"]["role"] },
    datasets: (datasets.data ?? []) as Dataset[],
    jobs: ((jobs.data ?? []) as Job[]).map((j) => ({ ...j, dataset_name: names.get(j.dataset_id ?? "") ?? undefined })),
    keys: (keys.data ?? []) as ApiKeyRow[],
    usage: [...byDay.values()].sort((a, b) => a.day.localeCompare(b.day)),
    members: ((members.data ?? []) as Omit<Member, "email">[]).map((m) => ({ ...m, email: m.user_id === user?.id ? user.email ?? "" : m.user_id.slice(0, 8) })),
    activeModel: { revision: active?.revision ?? "–", repo: active?.hf_repo ?? "lorde-tiny-aya" },
  };
}

export async function getJudgments(jobId: string): Promise<Judgment[]> {
  if (!supabaseEnabled()) return demoJudgments(jobId);
  const { supabaseServer } = await import("./supabase/server");
  const sb = await supabaseServer();
  const { data } = await sb.from("judgments").select("row_idx, gate, lang, score, decision, verdict").eq("job_id", jobId)
    .order("row_idx").limit(5000);
  return (data ?? []) as Judgment[];
}
