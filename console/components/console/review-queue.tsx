"use client";

import clsx from "clsx";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge, Button, Card } from "@/components/ui";
import { agreement } from "@/lib/agreement";
import { pct } from "@/lib/format";
import type { ReviewItem } from "@/lib/types";

const SCORES = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1];

export function ReviewQueue({ items, demo, orgId }: { items: ReviewItem[]; demo: boolean; orgId: string }) {
  const [i, setI] = useState(0);
  const [score, setScore] = useState<number | null>(null);
  const [labels, setLabels] = useState<Record<string, number>>({});
  const [msg, setMsg] = useState("");
  const item = items[i];
  const stats = useMemo(() => agreement(items.filter((x) => x.ref in labels).map((x) => ({ human: labels[x.ref]!, lorde: x.lorde_score }))), [labels, items]);

  const save = useCallback(async () => {
    if (!item || score === null) return;
    setLabels((l) => ({ ...l, [item.ref]: score }));
    if (!demo) {
      const { supabaseBrowser } = await import("@/lib/supabase/client");
      const sb = supabaseBrowser();
      const { data } = await sb.auth.getUser();
      const { error } = await sb.from("human_labels").insert({ org_id: orgId, item_ref: item.ref, kind: item.kind, score, labeler: data.user?.id });
      setMsg(error ? `Not saved: ${error.message}` : "Saved");
    }
    setScore(null);
    setI((x) => Math.min(items.length - 1, x + 1));
  }, [item, score, demo, orgId, items.length]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "TEXTAREA" || (e.target as HTMLElement)?.tagName === "INPUT") return;
      if (/^[0-9]$/.test(e.key)) setScore(Number(e.key) / 10);
      else if (e.key === "p" || e.key === "P") setScore(1);
      else if (e.key === "Enter") save();
      else if (e.key === "s" || e.key === "S" || e.key === "ArrowRight") { setScore(null); setI((x) => Math.min(items.length - 1, x + 1)); }
      else if (e.key === "ArrowLeft") { setScore(null); setI((x) => Math.max(0, x - 1)); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save, items.length]);

  if (!item) return null;
  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_300px]">
      <Card className="p-6">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="accent">{item.kind === "grounding" ? "Grounding" : "Reward"}</Badge>
          <Badge>{item.lang}</Badge>
          <span className="tnum ml-auto text-[13px] text-muted">{i + 1} / {items.length}</span>
        </div>
        <div className="mt-5 space-y-4 text-[15px] leading-relaxed" lang={item.lang} dir={item.lang === "ar" ? "rtl" : undefined}>
          {item.kind === "grounding" ? (
            <>
              <section><p className="font-mono text-[11px] uppercase text-muted">Source</p><p className="mt-1">{item.source_text}</p></section>
              <section><p className="font-mono text-[11px] uppercase text-muted">Question</p><p className="mt-1">{item.question}</p></section>
              <section><p className="font-mono text-[11px] uppercase text-muted">Answer</p><p className="mt-1 font-medium">{item.answer}</p></section>
            </>
          ) : (
            <>
              <section><p className="font-mono text-[11px] uppercase text-muted">Instruction</p><p className="mt-1">{item.instruction}</p></section>
              <section><p className="font-mono text-[11px] uppercase text-muted">Response</p><p className="mt-1 font-medium">{item.response}</p></section>
            </>
          )}
        </div>
        <p className="mt-6 text-[13px] text-muted">
          {item.kind === "grounding"
            ? "Is every claim supported by the source? Outside knowledge counts as unsupported. Pass is 0.7 or higher."
            : "Helpfulness, honesty and instruction following. Pass is 0.7 or higher."}
        </p>
        <div role="radiogroup" aria-label="Score" className="mt-4 flex flex-wrap gap-1.5">
          {SCORES.map((s) => (
            <button key={s} role="radio" aria-checked={score === s} onClick={() => setScore(s)}
              className={clsx("tnum h-10 w-12 rounded-xl border font-mono text-[13px] transition-colors",
                score === s ? (s >= 0.7 ? "border-pass bg-pass text-white" : "border-reject bg-reject text-white")
                  : "border-line hover:bg-wash")}>
              {s.toFixed(1)}
            </button>
          ))}
        </div>
        <div className="mt-5 flex flex-wrap items-center gap-2">
          <Button onClick={save} disabled={score === null}>Save and next</Button>
          <Button variant="secondary" onClick={() => { setScore(null); setI(Math.min(items.length - 1, i + 1)); }}>Skip</Button>
          {msg && <span role="status" className="text-[13px] text-muted">{msg}</span>}
        </div>
        <p className="mt-4 font-mono text-[11px] text-faint">Keys: 0–9 = 0.0–0.9 · P = 1.0 · Enter save · S skip · ← → move</p>
      </Card>
      <aside className="space-y-4">
        <Card className="p-5">
          <p className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted">Agreement with Lorde</p>
          <p className="tnum mt-3 text-[34px] font-medium tracking-[-0.04em]">{pct(stats.agreement)}</p>
          <p className="tnum text-sm text-muted">κ {stats.kappa === null ? "–" : stats.kappa.toFixed(2)} · {stats.n} labeled</p>
        </Card>
        <Card className="p-5 text-sm">
          <p className="font-medium">Labeled</p>
          <ul className="mt-3 space-y-2">
            {items.filter((x) => x.ref in labels).map((x) => {
              const h = labels[x.ref]!; const agree = (h >= 0.7) === (x.lorde_score >= 0.7);
              return (
                <li key={x.ref} className="tnum flex items-center justify-between gap-2">
                  <span className="truncate text-muted">{x.ref} · {x.lang}</span>
                  <span className="font-mono text-[12px]">you {h.toFixed(1)} · Lorde {x.lorde_score.toFixed(2)} {agree ? "✓" : "✕"}</span>
                </li>
              );
            })}
            {!Object.keys(labels).length && <li className="text-muted">Nothing yet. Lorde&apos;s score is hidden until you label.</li>}
          </ul>
        </Card>
      </aside>
    </div>
  );
}
