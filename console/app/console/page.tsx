import Link from "next/link";

import { JobStatusBadge, Kpi, PageHeader, Table } from "@/components/console/bits";
import { UsageChart } from "@/components/console/charts";
import { Card, LinkButton } from "@/components/ui";
import { gatePassRates, getWorkspace, listJobs, usageDaily } from "@/lib/data";
import { jobDatasetLabel } from "@/lib/datasets";
import { ago, nf, pct, when } from "@/lib/format";
import { weekTotals } from "@/lib/usage";

export const metadata = { title: "Overview" };
/** Planning estimate from spec 9.4 (one L4 GPU), not a bill: replaced by the measured Bench figure once published. */
const SPEC_COST_PER_M = 22;
const RATE_DAYS = 30;

export default async function Overview() {
  const [ws, usage, rates, recent] = await Promise.all([getWorkspace(), usageDaily(14), gatePassRates(RATE_DAYS), listJobs(0, { size: 5 })]);
  const { week, prev } = weekTotals(usage);
  const rate = (g: "grounding" | "reward") => (rates[g].judged ? rates[g].passed / rates[g].judged : null);
  const rateHint = (g: "grounding" | "reward") =>
    rates[g].judged ? `${nf.format(rates[g].passed)} of ${nf.format(rates[g].judged)} judgments, last ${RATE_DAYS} days` : `No ${g} judgments in the last ${RATE_DAYS} days`;
  const m = ws.activeModel;
  return (
    <>
      <PageHeader title="Overview" lead={`${ws.org.name} · usage and recent jobs at a glance.`}
        actions={<><LinkButton href="/console/playground" variant="secondary" size="sm">Playground</LinkButton>
          <LinkButton href="/console/jobs/new" size="sm">New job</LinkButton></>} />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label="Judgments this week" value={nf.format(week)}
          hint={prev ? `${week >= prev ? "+" : ""}${pct((week - prev) / prev)} vs the 7 days before` : "Last 7 days"} />
        <Kpi label="Pass rate · grounding" value={pct(rate("grounding"))} hint={rateHint("grounding")} />
        <Kpi label="Pass rate · reward" value={pct(rate("reward"))} hint={rateHint("reward")} />
        <Kpi label="Est. cost this week" value={`~$${((week / 1e6) * SPEC_COST_PER_M).toFixed(2)}`}
          hint={<>Estimate at ${SPEC_COST_PER_M} / 1M judgments (spec planning figure, 1× L4), not a bill</>} />
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-[2fr_1fr]">
        <Card className="p-5">
          <div className="flex items-center justify-between">
            <h2 className="font-medium">Judgments per day</h2>
            <span className="font-mono text-[11px] uppercase text-muted">14 days · UTC</span>
          </div>
          <div className="mt-4"><UsageChart data={usage} /></div>
        </Card>
        <Card className="flex flex-col p-5">
          <h2 className="font-medium">Active model</h2>
          {m ? (
            <>
              <p className="mt-3 font-mono text-[15px]">{m.repo ?? m.name ?? "–"}</p>
              <p className="font-mono text-[13px] text-accent">@{m.revision}</p>
              <dl className="mt-5 space-y-2 text-sm">
                {m.name && <div className="flex justify-between gap-3"><dt className="text-muted">Name</dt><dd className="truncate">{m.name}</dd></div>}
                <div className="flex justify-between"><dt className="text-muted">Promoted</dt><dd>{when(m.promoted_at)}</dd></div>
                <div className="flex justify-between"><dt className="text-muted">Gates</dt><dd>Reward · Grounding</dd></div>
                <div className="flex justify-between"><dt className="text-muted">Retention</dt><dd>{ws.org.retention_days} days</dd></div>
              </dl>
            </>
          ) : (
            <p className="mt-3 text-sm text-muted">
              No model revision is registered as active yet. The model the Gateway serves right now is shown on the{" "}
              <Link href="/status" className="text-accent hover:underline">Status</Link> page.
            </p>
          )}
          <Link href="/console/reports" className="mt-auto pt-5 text-sm font-medium text-accent hover:underline">Bench report →</Link>
        </Card>
      </div>
      <div className="mt-8 flex items-center justify-between">
        <h2 className="text-[18px] font-medium">Recent jobs</h2>
        <Link href="/console/jobs" className="text-sm font-medium text-accent hover:underline">All jobs ({nf.format(recent.total)})</Link>
      </div>
      <div className="mt-3">
        {recent.rows.length === 0 ? (
          <p className="text-sm text-muted">No jobs yet. <Link href="/console/datasets" className="text-accent hover:underline">Upload a dataset</Link> to start one.</p>
        ) : (
          <Table head={["Job", "Dataset", "Status", "Passed", "Pass rate", "Created"]}>
            {recent.rows.map((j) => (
              <tr key={j.id} className="hover:bg-wash/60">
                <td className="px-4 py-3 font-mono text-[13px]"><Link href={`/console/jobs/${j.id}`} className="text-accent hover:underline">{j.id.slice(0, 8)}</Link></td>
                <td className="max-w-[240px] truncate px-4 py-3">{jobDatasetLabel(j)}</td>
                <td className="px-4 py-3"><JobStatusBadge status={j.status} /></td>
                <td className="px-4 py-3">{j.counts.passed !== undefined ? nf.format(j.counts.passed) : "–"}</td>
                <td className="px-4 py-3">{pct(j.counts.pass_rate)}</td>
                <td className="px-4 py-3 text-muted">{ago(j.created_at)}</td>
              </tr>
            ))}
          </Table>
        )}
      </div>
    </>
  );
}
