import Link from "next/link";

import { Empty, JobStatusBadge, PageHeader, Table } from "@/components/console/bits";
import { LinkButton } from "@/components/ui";
import { getWorkspace } from "@/lib/data";
import { ago, nf, pct } from "@/lib/format";

export const metadata = { title: "Jobs" };

export default async function Jobs() {
  const ws = await getWorkspace();
  return (
    <>
      <PageHeader title="Jobs" lead="Gating jobs run CuratorKIT with Lorde as the judge. Three can be queued or running at once."
        actions={<LinkButton href="/console/jobs/new" size="sm">New job</LinkButton>} />
      {ws.jobs.length === 0 ? (
        <Empty title="No jobs yet">Upload a dataset, then start a job to get passed rows, rejected rows with reasons, and provenance.</Empty>
      ) : (
        <Table head={["Job", "Dataset", "Gates", "Status", "Rows", "Passed", "Pass rate", "Created"]} minWidth={860}>
          {ws.jobs.map((j) => (
            <tr key={j.id} className="hover:bg-wash/60">
              <td className="px-4 py-3 font-mono text-[13px]"><Link href={`/console/jobs/${j.id}`} className="text-accent hover:underline">{j.id.slice(0, 8)}</Link></td>
              <td className="max-w-[220px] truncate px-4 py-3">{j.dataset_name ?? "Hub dataset"}</td>
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
      )}
    </>
  );
}
