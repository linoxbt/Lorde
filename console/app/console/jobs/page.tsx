import Link from "next/link";

import { AutoRefresh } from "@/components/console/auto-refresh";
import { Empty, JobStatusBadge, PageHeader, Table } from "@/components/console/bits";
import { LinkButton } from "@/components/ui";
import { getWorkspace, JOBS_PER_PAGE, listJobs } from "@/lib/data";
import { jobDatasetLabel } from "@/lib/datasets";
import { ago, nf, pct } from "@/lib/format";
import { isPastEnd, pageCount, parsePage } from "@/lib/paging";

export const metadata = { title: "Jobs" };

export default async function Jobs(props: PageProps<"/console/jobs">) {
  const sp = await props.searchParams;
  const page = parsePage(sp.page);
  const [ws, jobs] = await Promise.all([getWorkspace(), listJobs(page)]);
  const pages = pageCount(jobs.total, JOBS_PER_PAGE);
  const active = jobs.rows.some((j) => j.status === "queued" || j.status === "running");
  return (
    <>
      <AutoRefresh active={active && ws.mode === "live"} every={10_000} />
      <PageHeader title="Jobs" lead="Gating jobs run CuratorKIT with Lorde as the judge. Three can be queued or running at once."
        actions={<LinkButton href="/console/jobs/new" size="sm">New job</LinkButton>} />
      {jobs.total === 0 ? (
        <Empty title="No jobs yet">Upload a dataset, then start a job to get passed rows, rejected rows with reasons, and provenance.</Empty>
      ) : isPastEnd(page, jobs.total, JOBS_PER_PAGE) ? (
        <Empty title={`Page ${page + 1} is past the end`}>
          There {pages === 1 ? "is 1 page" : `are ${pages} pages`} of jobs ({nf.format(jobs.total)} in all).{" "}
          <Link href={pages > 1 ? `?page=${pages}` : "/console/jobs"} className="text-accent hover:underline">Go to the last page</Link>
        </Empty>
      ) : (
        <>
          <Table head={["Job", "Dataset", "Gates", "Status", "Rows", "Passed", "Pass rate", "Created"]} minWidth={860}>
            {jobs.rows.map((j) => (
              <tr key={j.id} className="hover:bg-wash/60">
                <td className="px-4 py-3 font-mono text-[13px]"><Link href={`/console/jobs/${j.id}`} className="text-accent hover:underline">{j.id.slice(0, 8)}</Link></td>
                <td className="max-w-[220px] truncate px-4 py-3">{jobDatasetLabel(j)}</td>
                <td className="px-4 py-3 font-mono text-[12px] text-muted">
                  {j.config.gates.grounding !== null && <span className="mr-2">G≥{j.config.gates.grounding}</span>}
                  {j.config.gates.reward !== null && <span>R≥{j.config.gates.reward}</span>}
                </td>
                <td className="px-4 py-3"><JobStatusBadge status={j.status} /></td>
                <td className="px-4 py-3">{j.counts.rows_in !== undefined ? nf.format(j.counts.rows_in) : "–"}</td>
                <td className="px-4 py-3">{j.counts.passed !== undefined ? nf.format(j.counts.passed) : "–"}</td>
                <td className="px-4 py-3">{pct(j.counts.pass_rate)}</td>
                <td className="px-4 py-3 text-muted">{ago(j.created_at)}</td>
              </tr>
            ))}
          </Table>
          <nav aria-label="Pages" className="mt-3 flex items-center justify-end gap-2 text-[13px]">
            <span className="tnum mr-auto text-muted">{nf.format(jobs.total)} jobs</span>
            {page > 0
              ? <Link href={page > 1 ? `?page=${page}` : "/console/jobs"} className="rounded-full border border-line px-3 py-1.5">Previous</Link>
              : <span className="rounded-full border border-line px-3 py-1.5 opacity-40">Previous</span>}
            <span className="tnum text-muted">Page {page + 1} of {pages}</span>
            {page < pages - 1
              ? <Link href={`?page=${page + 2}`} className="rounded-full border border-line px-3 py-1.5">Next</Link>
              : <span className="rounded-full border border-line px-3 py-1.5 opacity-40">Next</span>}
          </nav>
        </>
      )}
    </>
  );
}
