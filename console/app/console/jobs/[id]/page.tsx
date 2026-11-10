import { notFound } from "next/navigation";

import { JobStatusBadge, Kpi, PageHeader } from "@/components/console/bits";
import { ReasonsChart, ScoreHistogram } from "@/components/console/charts";
import { JobActions } from "@/components/console/job-actions";
import { RowTable } from "@/components/console/row-table";
import { Card } from "@/components/ui";
import { getJudgments, getWorkspace } from "@/lib/data";
import { nf, pct, when } from "@/lib/format";

export const metadata = { title: "Job" };

export default async function JobDetail(props: PageProps<"/console/jobs/[id]">) {
  const { id } = await props.params;
  const ws = await getWorkspace();
  const job = ws.jobs.find((j) => j.id === id);
  if (!job) notFound();
  const rows = job.status === "succeeded" ? await getJudgments(id) : [];
  const c = job.counts;
  // The worker reports state, not row-level progress: running jobs get an indeterminate bar.
  const progress = job.status === "succeeded" ? 1 : job.status === "running" ? null : 0;
  return (
    <>
      <PageHeader title={`Job ${id.slice(0, 8)}`}
        lead={<span className="flex flex-wrap items-center gap-2"><JobStatusBadge status={job.status} />{job.dataset_name ?? "Hub dataset"} · {job.model_revision}</span>}
        actions={<JobActions id={id} status={job.status} demo={ws.mode === "demo"} />} />
      {job.error && <Card className="mb-6 border-[#f6cfb3] bg-reject-wash p-4 font-mono text-[13px] text-[#9a4300]">{job.error}</Card>}
      <Card className="mb-6 p-5">
        <div className="flex items-center justify-between text-sm">
          <span className="font-medium">Progress</span>
          <span className="tnum text-muted">{job.status === "running" ? "Running CuratorKIT with Lorde as the judge" : job.status}</span>
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
      {job.status === "succeeded" && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Kpi label="Rows in" value={nf.format(c.rows_in ?? 0)} />
            <Kpi label="Passed" value={nf.format(c.passed ?? 0)} hint={pct(c.pass_rate)} />
            <Kpi label="Rejected" value={nf.format(c.rejected ?? 0)} />
            <Kpi label="Judge errors" value={nf.format(c.judge_errors ?? 0)} hint="Structured outputs per gate" />
          </div>
          <div className="mt-4 grid gap-4 xl:grid-cols-2">
            <Card className="p-5"><h2 className="mb-4 font-medium">Rejection reasons</h2><ReasonsChart reasons={c.rejection_reasons ?? {}} /></Card>
            <Card className="p-5"><h2 className="mb-4 font-medium">Scores by language</h2><ScoreHistogram rows={rows} /></Card>
          </div>
          <h2 className="mb-3 mt-8 text-[18px] font-medium">Rows</h2>
          <RowTable rows={rows} storeContent={ws.org.store_content || ws.mode === "demo"} />
        </>
      )}
    </>
  );
}
