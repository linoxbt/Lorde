"use client";

import { useMemo, useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { Gate, HistCell, UsageDay } from "@/lib/types";
import { compact } from "@/lib/usage";

const AX = { fontSize: 11, fill: "#8a919e", fontFamily: "var(--font-mono)" };

export function UsageChart({ data }: { data: UsageDay[] }) {
  return (
    <div className="h-64" role="img" aria-label="Judgments per day over the last two weeks">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
          <defs>
            <linearGradient id="u" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#4338ca" stopOpacity={0.22} />
              <stop offset="100%" stopColor="#4338ca" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} stroke="#eef0f3" />
          <XAxis dataKey="day" tick={AX} tickLine={false} axisLine={false} tickFormatter={(d: string) => d.slice(5)} />
          <YAxis tick={AX} tickLine={false} axisLine={false} width={48} allowDecimals={false} tickFormatter={compact} />
          <Tooltip contentStyle={{ borderRadius: 12, border: "1px solid #e6e8ec", fontSize: 12 }} />
          <Area type="monotone" dataKey="judgments" stroke="#4338ca" strokeWidth={2} fill="url(#u)" name="Judgments" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ReasonsChart({ reasons }: { reasons: Record<string, number> }) {
  const data = Object.entries(reasons).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...data.map(([, n]) => n));
  const total = data.reduce((a, [, n]) => a + n, 0);
  if (!data.length) return <p className="text-sm text-muted">No rejections.</p>;
  return (
    <ul className="space-y-4" aria-label="Rejection reasons">
      {data.map(([reason, n]) => (
        <li key={reason}>
          <div className="flex items-baseline justify-between gap-3 text-[13px]">
            <code className="font-mono text-ink-2">{reason}</code>
            <span className="tnum text-muted">{n.toLocaleString("en-US")} · {((n / total) * 100).toFixed(0)}%</span>
          </div>
          <div className="mt-1.5 h-2.5 rounded-full bg-wash-2">
            <div className="h-full rounded-full bg-reject" style={{ width: `${(n / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

/** Score histogram from SQL aggregates (job_judgment_stats), per gate and language, split by the gate's decision. */
export function ScoreHistogram({ cells }: { cells: HistCell[] }) {
  const langs = useMemo(() => ["all", ...[...new Set(cells.map((c) => c.lang))].sort()], [cells]);
  const gates = useMemo(() => [...new Set(cells.map((c) => c.gate))].sort() as Gate[], [cells]);
  const [lang, setLang] = useState("all");
  const [gate, setGate] = useState<"all" | Gate>("all");
  const { data, unscored } = useMemo(() => {
    const bins = Array.from({ length: 10 }, (_, i) => ({ bin: (i / 10).toFixed(1), pass: 0, reject: 0 }));
    let unscored = 0;
    for (const c of cells) {
      if ((lang !== "all" && c.lang !== lang) || (gate !== "all" && c.gate !== gate)) continue;
      if (c.bin === null) { unscored += c.passed + c.rejected + c.undecided; continue; }
      const b = bins[Math.min(9, Math.max(0, c.bin))]!;
      b.pass += c.passed; b.reject += c.rejected + c.undecided;
    }
    return { data: bins, unscored };
  }, [cells, lang, gate]);
  if (!cells.length) return <p className="text-sm text-muted">No judgments recorded for this job.</p>;
  const pill = (on: boolean) => `rounded-full border px-2.5 py-1 font-mono text-[11px] uppercase ${on ? "border-accent bg-accent-wash text-accent" : "border-line text-muted hover:bg-wash"}`;
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label="Histogram filters">
        {langs.map((l) => (
          <button key={l} type="button" onClick={() => setLang(l)} aria-pressed={lang === l} className={pill(lang === l)}>{l}</button>
        ))}
        {gates.length > 1 && <span className="mx-1 w-px bg-line" aria-hidden />}
        {gates.length > 1 && (["all", ...gates] as const).map((g) => (
          <button key={g} type="button" onClick={() => setGate(g)} aria-pressed={gate === g} className={pill(gate === g)}>{g === "all" ? "both gates" : g}</button>
        ))}
      </div>
      <div className="h-56" role="img" aria-label={`Score distribution, ${lang}, ${gate === "all" ? "both gates" : gate}`}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
            <CartesianGrid vertical={false} stroke="#eef0f3" />
            <XAxis dataKey="bin" tick={AX} tickLine={false} axisLine={false} />
            <YAxis tick={AX} tickLine={false} axisLine={false} allowDecimals={false} tickFormatter={compact} />
            <Tooltip cursor={{ fill: "#f6f7f9" }} contentStyle={{ borderRadius: 12, border: "1px solid #e6e8ec", fontSize: 12 }} />
            <Bar dataKey="reject" stackId="s" fill="#d55e00" name="Reject" />
            <Bar dataKey="pass" stackId="s" fill="#009e73" radius={[4, 4, 0, 0]} name="Pass" />
          </BarChart>
        </ResponsiveContainer>
      </div>
      {unscored > 0 && <p className="mt-2 text-[12px] text-muted">{unscored.toLocaleString("en-US")} judgments had no score.</p>}
      {langs.length === 2 && langs[1] === "?" && <p className="mt-1 text-[12px] text-muted">Row language is not recorded yet, so every row is under “?”.</p>}
    </div>
  );
}
