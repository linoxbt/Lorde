import Link from "next/link";

import { JobStatusBadge, Kpi, PageHeader, Table } from "@/components/console/bits";
import { UsageChart } from "@/components/console/charts";
import { Card, LinkButton } from "@/components/ui";
import { getWorkspace } from "@/lib/data";
import { ago, nf, pct } from "@/lib/format";

export const metadata = { title: "Overview" };
const SPEC_COST_PER_M = 22; // spec 9.4 estimate for one L4; replaced by the measured Bench figure once published

export default async function Overview() {
  const ws = await getWorkspace();
  const week = ws.usage.slice(-7).reduce((a, d) => a + d.judgments, 0);
  const prev = ws.usage.slice(-14, -7).reduce((a, d) => a + d.judgments, 0);
  const done = ws.jobs.filter((j) => j.status === "succeeded");
  const rate = (gate: "grounding" | "reward") => {
    const js = done.filter((j) => j.config.gates[gate] !== null && j.counts.pass_rate != null);
    return js.length ? js.reduce((a, j) => a + (j.counts.pass_rate ?? 0), 0) / js.length : null;
  };
  return (
    <>
      <PageHeader title="Overview" lead={`${ws.org.name} · usage and recent jobs at a glance.`}
        actions={<><LinkButton href="/console/playground" variant="secondary" size="sm">Playground</LinkButton>
          <LinkButton href="/console/jobs/new" size="sm">New job</LinkButton></>} />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label="Judgments this week" value={nf.format(week)}
          hint={prev ? `${week >= prev ? "+" : ""}${pct((week - prev) / prev)} vs last week` : undefined} />
        <Kpi label="Pass rate · grounding" value={pct(rate("grounding"))} hint="Across succeeded jobs" />
        <Kpi label="Pass rate · reward" value={pct(rate("reward"))} hint="Across succeeded jobs" />
        <Kpi label="Est. cost this week" value={`$${((week / 1e6) * SPEC_COST_PER_M).toFixed(2)}`}
          hint={<>At ${SPEC_COST_PER_M} / 1M (spec estimate, 1× L4)</>} />
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-[2fr_1fr]">
        <Card className="p-5">
          <div className="flex items-center justify-between">
            <h2 className="font-medium">Judgments per day</h2>
            <span className="font-mono text-[11px] uppercase text-muted">14 days</span>
          </div>
          <div className="mt-4"><UsageChart data={ws.usage} /></div>
        </Card>
        <Card className="flex flex-col p-5">
          <h2 className="font-medium">Active model</h2>
          <p className="mt-3 font-mono text-[15px]">{ws.activeModel.repo}</p>
          <p className="font-mono text-[13px] text-accent">@{ws.activeModel.revision}</p>
          <dl className="mt-5 space-y-2 text-sm">
            <div className="flex justify-between"><dt className="text-muted">Base</dt><dd>Tiny Aya Global 3.35B</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Gates</dt><dd>Reward · Grounding</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Structured outputs</dt><dd>On</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Retention</dt><dd>{ws.org.retention_days} days</dd></div>
          </dl>
          <Link href="/console/reports" className="mt-auto pt-5 text-sm font-medium text-accent hover:underline">Bench report →</Link>
        </Card>
      </div>
      <div className="mt-8 flex items-center justify-between">
        <h2 className="text-[18px] font-medium">Recent jobs</h2>
        <Link href="/console/jobs" className="text-sm font-medium text-accent hover:underline">All jobs</Link>
      </div>
      <div className="mt-3">
        <Table head={["Job", "Dataset", "Status", "Passed", "Pass rate", "Created"]}>
          {ws.jobs.slice(0, 5).map((j) => (
            <tr key={j.id} className="hover:bg-wash/60">
              <td className="px-4 py-3 font-mono text-[13px]"><Link href={`/console/jobs/${j.id}`} className="text-accent hover:underline">{j.id.slice(0, 8)}</Link></td>
              <td className="max-w-[240px] truncate px-4 py-3">{j.dataset_name ?? "–"}</td>
              <td className="px-4 py-3"><JobStatusBadge status={j.status} /></td>
              <td className="px-4 py-3">{j.counts.passed !== undefined ? nf.format(j.counts.passed) : "–"}</td>
              <td className="px-4 py-3">{pct(j.counts.pass_rate)}</td>
              <td className="px-4 py-3 text-muted">{ago(j.created_at)}</td>
            </tr>
          ))}
        </Table>
      </div>
    </>
  );
}
