import { FileDown } from "lucide-react";
import Link from "next/link";

import { JobStatusBadge, PageHeader, Table } from "@/components/console/bits";
import { Badge, Card, LinkButton } from "@/components/ui";
import { getWorkspace, listJobs } from "@/lib/data";
import { jobDatasetLabel } from "@/lib/datasets";
import { pct, when } from "@/lib/format";

export const metadata = { title: "Reports" };

const met = (m: boolean | null) => (m === null ? <Badge>Not measured</Badge> : m ? <Badge tone="pass">Met</Badge> : <Badge tone="reject">Not met</Badge>);

export default async function Reports() {
  const [ws, jobs] = await Promise.all([getWorkspace(), listJobs(0, { status: "succeeded" })]);
  const done = jobs.rows;
  const m = ws.activeModel;
  const acceptance = Array.isArray(m?.bench?.acceptance) ? m.bench.acceptance : [];
  return (
    <>
      <PageHeader title="Reports" lead="Evidence you can hand to a reviewer: the active model's Bench report, job reports, and CuratorKIT provenance." />
      <div className="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
        <Card className="p-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="font-medium">Lorde Bench{m ? ` · ${m.repo ?? m.name ?? "model"}@${m.revision}` : ""}</h2>
              <p className="mt-1 text-sm text-muted">Acceptance criteria measured for the active model revision.</p>
            </div>
            <LinkButton href="/bench" variant="secondary" size="sm">Method</LinkButton>
          </div>
          {acceptance.length ? (
            <ul className="mt-5 divide-y divide-line text-sm">
              {acceptance.map((c) => (
                <li key={c.criterion} className="flex items-center justify-between gap-3 py-2.5">
                  <span>{c.criterion}{c.blocking === false && <span className="ml-1 text-[12px] text-muted">(advisory)</span>}</span>
                  <span className="flex items-center gap-2"><span className="font-mono text-[12px] text-muted">{c.value}</span>{met(c.met)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-5 rounded-xl bg-wash p-4 text-sm text-muted">
              {m ? "No Bench results are recorded for this revision yet." : "No model revision is registered as active yet."}{" "}
              Results appear here once forge/06 and bench/run_bench.py publish them to the model registry. The criteria every
              release must meet are on the <a href="/bench" className="text-accent hover:underline">Bench page</a>.
            </p>
          )}
        </Card>
        <Card className="p-6">
          <h2 className="font-medium">Provenance</h2>
          <p className="mt-2 text-sm text-muted">Every job ships CuratorKIT&apos;s manifest and <code className="rounded bg-wash px-1 font-mono text-[12px]">lexsi_provenance.json</code>, so each passed row traces back to its source, the gates it cleared and the model revision that judged it.</p>
          <ul className="mt-5 space-y-2 text-sm">
            {["report.md · counts, reasons, stage counts", "passed.jsonl · rows with scores", "rejected.jsonl · rows with reasons", "manifest.json · CuratorKIT run manifest", "lexsi_provenance.json · provenance chain"].map((f) => (
              <li key={f} className="flex items-center gap-2"><FileDown className="size-4 text-accent" aria-hidden />{f}</li>
            ))}
          </ul>
        </Card>
      </div>
      <h2 className="mb-3 mt-10 text-[18px] font-medium">Job reports</h2>
      {jobs.total > done.length && <p className="mb-2 text-[12px] text-muted">Showing the {done.length} newest of {jobs.total} succeeded jobs.</p>}
      <Table head={["Job", "Dataset", "Status", "Pass rate", "Judge errors", "Finished", ""]} minWidth={760}>
        {done.map((j) => (
          <tr key={j.id} className="hover:bg-wash/60">
            <td className="px-4 py-3 font-mono text-[13px]">{j.id.slice(0, 8)}</td>
            <td className="max-w-[240px] truncate px-4 py-3">{jobDatasetLabel(j)}</td>
            <td className="px-4 py-3"><JobStatusBadge status={j.status} /></td>
            <td className="px-4 py-3">{pct(j.counts.pass_rate)}</td>
            <td className="px-4 py-3">{j.counts.judge_errors ?? 0}</td>
            <td className="px-4 py-3 text-muted">{when(j.finished_at)}</td>
            <td className="px-4 py-3 text-right"><Link href={`/console/jobs/${j.id}`} className="text-[13px] font-medium text-accent hover:underline">Open</Link></td>
          </tr>
        ))}
      </Table>
    </>
  );
}
