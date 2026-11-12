"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTransition } from "react";

import { DecisionBadge, Table } from "@/components/console/bits";
import type { Judgment, JudgmentFilters } from "@/lib/types";

/** Judgment rows, filtered and paginated on the server: filters live in the URL (?gate=&decision=&lang=&page=). */
export function RowTable({ rows, total, filters, langs, perPage, storeContent }:
  { rows: Judgment[]; total: number; filters: JudgmentFilters; langs: string[]; perPage: number; storeContent: boolean }) {
  const router = useRouter(), pathname = usePathname(), sp = useSearchParams();
  const [pending, start] = useTransition();
  const href = (patch: Record<string, string | null>) => {
    const q = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === "" || v === "all") q.delete(k); else q.set(k, v);
    }
    const s = q.toString();
    return s ? `${pathname}?${s}` : pathname;
  };
  const set = (k: string, v: string) => start(() => router.push(href({ [k]: v, page: null }), { scroll: false }));
  const pages = Math.max(1, Math.ceil(total / perPage));
  const page = Math.min(filters.page, pages - 1);
  const sel = "h-9 rounded-full border border-line bg-white px-3 text-[13px]";
  return (
    <div aria-busy={pending}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="f-dec">Decision</label>
        <select id="f-dec" className={sel} value={filters.decision ?? "all"} onChange={(e) => set("decision", e.target.value)}>
          <option value="all">All decisions</option><option value="pass">Passed</option><option value="reject">Rejected</option>
          <option value="none">No decision</option>
        </select>
        <label className="sr-only" htmlFor="f-gate">Gate</label>
        <select id="f-gate" className={sel} value={filters.gate ?? "all"} onChange={(e) => set("gate", e.target.value)}>
          <option value="all">Both gates</option><option value="grounding">Grounding</option><option value="reward">Reward</option>
        </select>
        <label className="sr-only" htmlFor="f-lang">Language</label>
        <select id="f-lang" className={sel} value={filters.lang ?? "all"} onChange={(e) => set("lang", e.target.value)}>
          <option value="all">All languages</option>
          {langs.map((l) => <option key={l} value={l}>{l === "?" ? "Unknown" : l}</option>)}
        </select>
        <span className="tnum ml-auto text-[13px] text-muted">{pending ? "Loading…" : `${total.toLocaleString("en-US")} rows`}</span>
      </div>
      <Table head={["Row", "Gate", "Lang", "Score", "Decision", "Verdict / reason", ...(storeContent ? ["Preview"] : [])]} minWidth={760}>
        {rows.map((r) => (
          <tr key={`${r.row_idx}-${r.gate}`} className="hover:bg-wash/60">
            <td className="px-4 py-2.5 font-mono text-[13px]">{r.row_idx}</td>
            <td className="px-4 py-2.5">{r.gate}</td>
            <td className="px-4 py-2.5 font-mono text-[12px] uppercase">{r.lang ?? "–"}</td>
            <td className="px-4 py-2.5">{r.score?.toFixed(2) ?? "–"}</td>
            <td className="px-4 py-2.5"><DecisionBadge pass={r.decision} /></td>
            <td className="px-4 py-2.5 font-mono text-[12px] text-muted">{r.verdict ?? r.reason ?? "–"}</td>
            {storeContent && <td className="max-w-[320px] truncate px-4 py-2.5 text-muted">{r.preview ?? "–"}</td>}
          </tr>
        ))}
        {!rows.length && <tr><td colSpan={storeContent ? 7 : 6} className="px-4 py-6 text-center text-muted">No rows match these filters.</td></tr>}
      </Table>
      <nav aria-label="Pages" className="mt-3 flex items-center justify-end gap-2 text-[13px]">
        {page > 0
          ? <Link scroll={false} href={href({ page: page - 1 ? String(page) : null })} className="rounded-full border border-line px-3 py-1.5">Previous</Link>
          : <span className="rounded-full border border-line px-3 py-1.5 opacity-40">Previous</span>}
        <span className="tnum text-muted">Page {page + 1} of {pages}</span>
        {page < pages - 1
          ? <Link scroll={false} href={href({ page: String(page + 2) })} className="rounded-full border border-line px-3 py-1.5">Next</Link>
          : <span className="rounded-full border border-line px-3 py-1.5 opacity-40">Next</span>}
      </nav>
      {!storeContent && <p className="mt-2 text-[12px] text-muted">Row text is not stored in the database (scores only). Download passed.jsonl and rejected.jsonl for the content.</p>}
    </div>
  );
}
