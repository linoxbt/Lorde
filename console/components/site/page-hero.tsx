import type { ReactNode } from "react";

import { Container, Eyebrow } from "@/components/ui";

export function PageHero({ eyebrow, title, lead, children }:
  { eyebrow: string; title: ReactNode; lead?: ReactNode; children?: ReactNode }) {
  return (
    <section className="relative overflow-hidden border-b border-line">
      <div aria-hidden className="pointer-events-none absolute -right-40 -top-40 size-[560px] rounded-full border border-accent/15" />
      <div aria-hidden className="pointer-events-none absolute -right-20 -top-20 size-[320px] rounded-full border border-accent/20" />
      <Container className="relative py-20 sm:py-24">
        <Eyebrow>{eyebrow}</Eyebrow>
        <h1 className="mt-5 max-w-3xl text-[44px] font-medium leading-[1.02] tracking-[-0.045em] sm:text-[60px]">{title}</h1>
        {lead && <p className="mt-5 max-w-2xl text-[18px] leading-relaxed text-muted">{lead}</p>}
        {children && <div className="mt-8 flex flex-wrap gap-3">{children}</div>}
      </Container>
    </section>
  );
}

export function Prose({ children }: { children: ReactNode }) {
  return <div className="space-y-4 text-[16px] leading-relaxed text-ink-2 [&_code]:rounded [&_code]:bg-wash [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[13.5px]">{children}</div>;
}

export function DataTable({ head, rows, className = "" }: { head: string[]; rows: ReactNode[][]; className?: string }) {
  return (
    <div className={`overflow-x-auto rounded-[var(--radius-card)] border border-line ${className}`}>
      <table className="w-full min-w-[560px] text-left text-[14px]">
        <thead className="bg-wash text-[12px] uppercase tracking-[0.06em] text-muted">
          <tr>{head.map((h) => <th key={h} scope="col" className="px-4 py-3 font-medium">{h}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((r, i) => (
            <tr key={i} className="align-top">{r.map((c, j) => <td key={j} className="tnum px-4 py-3">{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
