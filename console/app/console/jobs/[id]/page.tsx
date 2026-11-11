import { notFound } from "next/navigation";

import { AutoRefresh } from "@/components/console/auto-refresh";
import { JobStatusBadge, Kpi, PageHeader } from "@/components/console/bits";
import { ReasonsChart, ScoreHistogram } from "@/components/console/charts";
import { JobActions } from "@/components/console/job-actions";
import { RowTable } from "@/components/console/row-table";
import { Card } from "@/components/ui";
import { getJob, getWorkspace, jobStats, judgmentsPage, ROWS_PER_PAGE } from "@/lib/data";
import { nf, pct, when } from "@/lib/format";
import type { JudgmentFilters } from "@/lib/types";

export const metadata = { title: "Job" };

const one = (v: string | string[] | undefined) => (typeof v === "string" ? v : undefined);

function filtersFrom(sp: Record<string, string | string[] | undefined>): JudgmentFilters {
  const gate = one(sp.gate), decision = one(sp.decision), lang = one(sp.lang);
  const page = Math.max(1, Math.min(100_000, Number.parseInt(one(sp.page) ?? "1", 10) || 1)) - 1;
  return {
    gate: gate === "reward" || gate === "grounding" ? gate : undefined,
    decision: decision === "pass" || decision === "reject" || decision === "none" ? decision : undefined,
    lang: lang && /^[\w?-]{1,16}$/.test(lang) ? lang : undefined,
    page,
  };
}

export default async function JobDetail(props: PageProps<"/console/jobs/[id]">) {
  const [{ id }, sp] = await Promise.all([props.params, props.searchParams]);
  const [ws, job] = await Promise.all([getWorkspace(), getJob(id)]);
  if (!job) notFound();
  const filters = filtersFrom(sp);
  const done = job.status === "succeeded";
  const [page, cells] = done ? await Promise.all([judgmentsPage(id, filters), jobStats(id)]) : [null, []];
  const langs = [...new Set(cells.map((c) => c.lang))].sort();
  const c = job.counts;
  const active = job.status === "queued" || job.status === "running";
  // The worker reports state, not row-level progress: running jobs get an indeterminate bar.
  const progress = done ? 1 : job.status === "running" ? null : 0;
  return (
    <>
      <AutoRefresh active={active && ws.mode === "live"} />
      <PageHeader title={`Job ${id.slice(0, 8)}`}
        lead={<span className="flex flex-wrap items-center gap-2"><JobStatusBadge status={job.status} />{job.dataset_name ?? "Hub dataset"}{job.model_revision ? ` · ${job.model_revision}` : ""}</span>}
        actions={<JobActions id={id} status={job.status} demo={ws.mode === "demo"} cancelRequested={job.cancel_requested} />} />
      {job.error && <Card className="mb-6 border-[#f6cfb3] bg-reject-wash p-4 font-mono text-[13px] text-[#9a4300]">{job.error}</Card>}
      <Card className="mb-6 p-5">
        <div className="flex items-center justify-between text-sm">
          <span className="font-medium">Progress</span>
          <span className="tnum text-muted">
            {job.status === "running" ? "Running CuratorKIT with Lorde as the judge" : job.status}
            {active && ws.mode === "live" ? " · refreshes every 5 s" : ""}
          </span>
        </div>
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-wash-2" role="progressbar" aria-valuemin={0} aria-valuemax={100}
          aria-valuenow={progress === null ? undefined : Math.round(progress * 100)} aria-valuetext={progress === null ? "In progress" : undefined}>
          <div className={`h-full rounded-full ${job.status === "failed" ? "bg-reject" : "bg-accent"} ${progress === null ? "w-1/3 animate-[indeterminate_1.4s_ease-in-out_infinite]" : ""}`}
            style={progress === null ? undefined : { width: `${progress * 100}%` }} />
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-3 text-[13px] sm:grid-cols-4">
          <div><dt className="text-muted">Created</dt><dd>{when(job.created_at)}</dd></div>
          <div><dt className="text-muted">Started</dt><dd>{when(job.started_at)}</dd></div>
          <div><dt className="text-muted">Finished</dt><dd>{when(job.finished_at)}</dd></div>
          <div><dt className="text-muted">Gates</dt><dd className="font-mono">
            {job.config.gates.grounding !== null ? `grounding ≥ ${job.config.gates.grounding} ` : ""}
            {job.config.gates.reward !== null ? `reward ≥ ${job.config.gates.reward}` : ""}</dd></div>
        </dl>
      </Card>
      {done && page && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Kpi label="Rows in" value={nf.format(c.rows_in ?? 0)} />
            <Kpi label="Passed" value={nf.format(c.passed ?? 0)} hint={pct(c.pass_rate)} />
            <Kpi label="Rejected" value={nf.format(c.rejected ?? 0)} />
            <Kpi label="Judge errors" value={nf.format(c.judge_errors ?? 0)} hint="Replies the gate could not use" />
          </div>
          <div className="mt-4 grid gap-4 xl:grid-cols-2">
            <Card className="p-5"><h2 className="mb-4 font-medium">Rejection reasons</h2><ReasonsChart reasons={c.rejection_reasons ?? {}} /></Card>
            <Card className="p-5"><h2 className="mb-4 font-medium">Scores by language</h2><ScoreHistogram cells={cells} /></Card>
          </div>
          <h2 className="mb-3 mt-8 text-[18px] font-medium">Rows</h2>
          <RowTable rows={page.rows} total={page.total} filters={filters} langs={langs} perPage={ROWS_PER_PAGE}
            storeContent={ws.mode === "demo"} />
        </>
      )}
    </>
  );
}
