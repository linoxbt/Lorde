import type { ReactNode } from "react";

import { Badge, Card } from "@/components/ui";
import type { JobStatus } from "@/lib/types";

export function PageHeader({ title, lead, actions }: { title: string; lead?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-[28px] font-medium tracking-[-0.03em]">{title}</h1>
        {lead && <p className="mt-1.5 max-w-2xl text-[15px] text-muted">{lead}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Kpi({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <Card className="p-5">
      <p className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted">{label}</p>
      <p className="tnum mt-3 text-[28px] font-medium tracking-[-0.03em]">{value}</p>
      {hint && <p className="mt-1 text-[13px] text-muted">{hint}</p>}
    </Card>
  );
}

const STATUS: Record<JobStatus, { tone: "pass" | "reject" | "accent" | "neutral" | "warn"; label: string }> = {
  queued: { tone: "neutral", label: "Queued" }, running: { tone: "accent", label: "Running" },
  succeeded: { tone: "pass", label: "Succeeded" }, failed: { tone: "reject", label: "Failed" },
  cancelled: { tone: "warn", label: "Cancelled" },
};

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const s = STATUS[status];
  return (
    <Badge tone={s.tone}>
      {status === "running" && <span className="size-1.5 animate-pulse rounded-full bg-accent" aria-hidden />}
      {s.label}
    </Badge>
  );
}

export function DecisionBadge({ pass }: { pass: boolean | null }) {
  if (pass === null) return <Badge>–</Badge>;
  return <Badge tone={pass ? "pass" : "reject"}>{pass ? "✓ Pass" : "✕ Reject"}</Badge>;
}

export function Table({ head, children, minWidth = 640 }: { head: ReactNode[]; children: ReactNode; minWidth?: number }) {
  return (
    <div className="overflow-x-auto rounded-[var(--radius-card)] border border-line">
      <table className="w-full text-left text-[14px]" style={{ minWidth }}>
        <thead className="bg-wash text-[12px] text-muted">
          <tr>{head.map((h, i) => <th key={i} scope="col" className="px-4 py-2.5 font-medium">{h}</th>)}</tr>
        </thead>
        <tbody className="tnum divide-y divide-line">{children}</tbody>
      </table>
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <Card className="flex flex-col items-center px-6 py-14 text-center">
      <p className="font-medium">{title}</p>
      {children && <div className="mt-2 max-w-md text-sm text-muted">{children}</div>}
    </Card>
  );
}
