"use client";

import { useMemo, useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { Judgment, UsageDay } from "@/lib/types";

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
          <YAxis tick={AX} tickLine={false} axisLine={false} width={48} tickFormatter={(v: number) => `${Math.round(v / 1000)}k`} />
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

/** Score histogram for one language (or all), split by decision, with the threshold marked. */
export function ScoreHistogram({ rows, threshold = 0.7 }: { rows: Judgment[]; threshold?: number }) {
  const langs = useMemo(() => ["all", ...[...new Set(rows.map((r) => r.lang ?? "?"))].sort()], [rows]);
  const [lang, setLang] = useState("all");
  const data = useMemo(() => {
    const bins = Array.from({ length: 10 }, (_, i) => ({ bin: (i / 10).toFixed(1), pass: 0, reject: 0 }));
    for (const r of rows) {
      if (r.score === null || (lang !== "all" && r.lang !== lang)) continue;
      const b = bins[Math.min(9, Math.floor(r.score * 10))]!;
      if (r.score >= threshold) b.pass++; else b.reject++;
    }
    return bins;
  }, [rows, lang, threshold]);
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label="Language">
        {langs.map((l) => (
          <button key={l} type="button" onClick={() => setLang(l)} aria-pressed={lang === l}
            className={`rounded-full border px-2.5 py-1 font-mono text-[11px] uppercase ${lang === l ? "border-accent bg-accent-wash text-accent" : "border-line text-muted hover:bg-wash"}`}>
            {l}
          </button>
        ))}
      </div>
      <div className="h-56" role="img" aria-label={`Score distribution, ${lang}`}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
            <CartesianGrid vertical={false} stroke="#eef0f3" />
            <XAxis dataKey="bin" tick={AX} tickLine={false} axisLine={false} />
            <YAxis tick={AX} tickLine={false} axisLine={false} allowDecimals={false} />
            <Tooltip cursor={{ fill: "#f6f7f9" }} contentStyle={{ borderRadius: 12, border: "1px solid #e6e8ec", fontSize: 12 }} />
            <Bar dataKey="reject" stackId="s" fill="#d55e00" name="Reject" />
            <Bar dataKey="pass" stackId="s" fill="#009e73" radius={[4, 4, 0, 0]} name="Pass" />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
