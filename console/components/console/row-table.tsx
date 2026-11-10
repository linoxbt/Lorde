"use client";

import { useMemo, useState } from "react";

import { DecisionBadge, Table } from "@/components/console/bits";
import type { Judgment } from "@/lib/types";

const PAGE = 25;

export function RowTable({ rows, storeContent }: { rows: Judgment[]; storeContent: boolean }) {
  const [decision, setDecision] = useState<"all" | "pass" | "reject">("all");
  const [gate, setGate] = useState<"all" | "reward" | "grounding">("all");
  const [lang, setLang] = useState("all");
  const [page, setPage] = useState(0);
  const langs = useMemo(() => [...new Set(rows.map((r) => r.lang ?? "?"))].sort(), [rows]);
  const shown = rows.filter((r) => (decision === "all" || (decision === "pass") === r.decision)
    && (gate === "all" || r.gate === gate) && (lang === "all" || r.lang === lang));
  const pages = Math.max(1, Math.ceil(shown.length / PAGE));
  const sel = "h-9 rounded-full border border-line bg-white px-3 text-[13px]";
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="f-dec">Decision</label>
        <select id="f-dec" className={sel} value={decision} onChange={(e) => { setDecision(e.target.value as typeof decision); setPage(0); }}>
          <option value="all">All decisions</option><option value="pass">Passed</option><option value="reject">Rejected</option>
        </select>
        <label className="sr-only" htmlFor="f-gate">Gate</label>
        <select id="f-gate" className={sel} value={gate} onChange={(e) => { setGate(e.target.value as typeof gate); setPage(0); }}>
          <option value="all">Both gates</option><option value="grounding">Grounding</option><option value="reward">Reward</option>
        </select>
        <label className="sr-only" htmlFor="f-lang">Language</label>
        <select id="f-lang" className={sel} value={lang} onChange={(e) => { setLang(e.target.value); setPage(0); }}>
          <option value="all">All languages</option>{langs.map((l) => <option key={l} value={l}>{l}</option>)}
        </select>
        <span className="tnum ml-auto text-[13px] text-muted">{shown.length} rows</span>
      </div>
      <Table head={["Row", "Gate", "Lang", "Score", "Decision", "Verdict / reason", ...(storeContent ? ["Preview"] : [])]} minWidth={760}>
        {shown.slice(page * PAGE, page * PAGE + PAGE).map((r) => (
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
      </Table>
      <div className="mt-3 flex items-center justify-end gap-2 text-[13px]">
        <button className="rounded-full border border-line px-3 py-1.5 disabled:opacity-40" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
        <span className="tnum text-muted">Page {page + 1} of {pages}</span>
        <button className="rounded-full border border-line px-3 py-1.5 disabled:opacity-40" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>Next</button>
      </div>
      {!storeContent && <p className="mt-2 text-[12px] text-muted">Row text is not stored in the database (scores only). Download passed.jsonl and rejected.jsonl for the content.</p>}
    </div>
  );
}
