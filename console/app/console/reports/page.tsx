import { FileDown } from "lucide-react";
import Link from "next/link";

import { JobStatusBadge, PageHeader, Table } from "@/components/console/bits";
import { Badge, Card, LinkButton } from "@/components/ui";
import { getWorkspace } from "@/lib/data";
import { pct, when } from "@/lib/format";

export const metadata = { title: "Reports" };

const CRITERIA = [
  ["Gate-usable rate, unconstrained", "≥ 99.0%"], ["Gate-usable rate, structured outputs", "100%"],
  ["Decision agreement vs teacher", "≥ 90%"], ["Cohen's κ vs teacher", "≥ 0.75"], ["Grounding verdict accuracy", "≥ 85%"],
  ["Human agreement", "≥ teacher's − 3 points"], ["Per-language agreement", "≥ 85% everywhere"],
  ["Downstream F1 vs teacher-gated", "within 2%"], ["Adversarial-100 pass rate", "≤ 5%"],
];

export default async function Reports() {
  const ws = await getWorkspace();
  const done = ws.jobs.filter((j) => j.status === "succeeded");
  return (
    <>
      <PageHeader title="Reports" lead="Evidence you can hand to a reviewer: the active model's Bench report, job reports, and CuratorKIT provenance." />
      <div className="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
        <Card className="p-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="font-medium">Lorde Bench · {ws.activeModel.repo}@{ws.activeModel.revision}</h2>
              <p className="mt-1 text-sm text-muted">Acceptance criteria for the active revision.</p>
            </div>
            <LinkButton href="/bench" variant="secondary" size="sm">Method</LinkButton>
          </div>
          <ul className="mt-5 divide-y divide-line text-sm">
            {CRITERIA.map(([c, t]) => (
              <li key={c} className="flex items-center justify-between gap-3 py-2.5">
                <span>{c}</span>
                <span className="flex items-center gap-2"><span className="font-mono text-[12px] text-muted">{t}</span>
                  <Badge tone="neutral">Published with release</Badge></span>
              </li>
            ))}
          </ul>
          <p className="mt-4 text-[12px] text-muted">Measured values appear here once forge/06 and bench/run_bench.py publish results for this revision.</p>
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
      <Table head={["Job", "Dataset", "Status", "Pass rate", "Judge errors", "Finished", ""]} minWidth={760}>
        {done.map((j) => (
          <tr key={j.id} className="hover:bg-wash/60">
            <td className="px-4 py-3 font-mono text-[13px]">{j.id.slice(0, 8)}</td>
            <td className="max-w-[240px] truncate px-4 py-3">{j.dataset_name ?? "–"}</td>
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
