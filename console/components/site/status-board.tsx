"use client";

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge, Button, Card } from "@/components/ui";

type Check = { name: string; state: "ok" | "degraded" | "down" | "demo"; detail: string; ms?: number };
type Data = { summary: { state: string; label: string }; checks: Check[] };
const TONE = { ok: "pass", degraded: "warn", down: "reject", demo: "accent" } as const;
const WORD = { ok: "Operational", degraded: "Degraded", down: "Down", demo: "Demo mode" };

export function StatusBoard() {
  const [d, setD] = useState<Data | null>(null);
  const [at, setAt] = useState<string>("");
  const load = useCallback(() => {
    fetch("/api/status", { cache: "no-store" }).then((r) => r.json()).then((x: Data) => {
      setD(x);
      setAt(new Date().toLocaleTimeString());
    }).catch(() => setD({ summary: { state: "down", label: "Status unavailable" }, checks: [] }));
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
