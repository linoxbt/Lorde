"use client";

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge, Button, Card } from "@/components/ui";
import type { Check, ServedModel } from "@/lib/status";

type Data = { summary: { state: string; label: string }; checks: Check[]; model?: ServedModel | null };
const TONE = { ok: "pass", degraded: "warn", down: "reject", unknown: "neutral", demo: "accent" } as const;
const WORD = { ok: "Responding", degraded: "Degraded", down: "Down", unknown: "Unknown", demo: "Demo mode" };

export function StatusBoard() {
  const [d, setD] = useState<Data | null>(null);
  const [at, setAt] = useState<string>("");
  const load = useCallback(() => {
    fetch("/api/status", { cache: "no-store" }).then((r) => r.json()).then((x: Data) => {
      setD(x);
      setAt(new Date().toLocaleTimeString());
    }).catch(() => setD({ summary: { state: "unknown", label: "Status unknown: could not reach this site's status check" }, checks: [] }));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);
  return (
    <div className="space-y-6">
      <Card className="flex flex-col gap-4 p-6 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-[22px] font-medium tracking-[-0.02em]">{d?.summary.label ?? "Checking…"}</p>
          <p className="mt-1 text-sm text-muted" aria-live="polite">{at ? `Last checked ${at}` : "Running checks"}</p>
          {d?.model && (
            <p className="mt-1 text-sm text-muted">
              Model: <span className="font-mono text-[13px] text-ink">{d.model.label}</span>
              {d.model.source === "config" && " (configured)"}
            </p>
          )}
          {d?.model?.notice && (
            <p role="note" className="mt-2"><Badge tone="warn">{d.model.notice}</Badge></p>
          )}
        </div>
        <Button variant="secondary" size="sm" onClick={load}><RefreshCw className="size-3.5" aria-hidden />Refresh</Button>
      </Card>
      <ul className="divide-y divide-line rounded-[var(--radius-card)] border border-line">
        {(d?.checks ?? []).map((c) => (
          <li key={c.name} className="flex flex-col gap-2 px-6 py-5 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="font-medium">{c.name}</p>
              <p className="text-sm text-muted">{c.detail}</p>
            </div>
            <div className="flex items-center gap-3">
              {c.ms !== undefined && <span className="tnum font-mono text-[12px] text-faint">{c.ms} ms</span>}
              <Badge tone={TONE[c.state]}>{WORD[c.state]}</Badge>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
