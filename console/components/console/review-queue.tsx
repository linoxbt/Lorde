"use client";

import clsx from "clsx";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ConnectKey } from "@/components/console/connect-key";
import { Badge, Button, Card } from "@/components/ui";
import { agreement } from "@/lib/agreement";
import { pct } from "@/lib/format";
import { nextUnlabelled } from "@/lib/review";
import { errorMessage, gatewayFetch, getKey } from "@/lib/gateway-client";
import type { Gate, ReviewItem, ReviewLabel } from "@/lib/types";

const SCORES = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1];
type Text = { instruction?: string; input?: string; output?: string };
type Label = { kind: Gate; human: number; lorde: number };

/** Find the rows' text in a job's passed/rejected files (signed Gateway URLs), streaming and stopping once all are found. */
async function loadRowText(jobId: string, rows: Set<number>): Promise<Map<number, Text>> {
  const r = await gatewayFetch(`/v1/jobs/${jobId}/results`);
  if (!r.ok) throw new Error(await errorMessage(r));
  const files = ((await r.json()) as { files?: Record<string, string> }).files ?? {};
  const found = new Map<number, Text>();
  for (const name of ["rejected", "passed"]) {
    const url = files[name];
    if (!url || found.size >= rows.size) continue;
    const res = await fetch(url);
    if (!res.ok || !res.body) throw new Error(`could not read ${name}.jsonl (${res.status})`);
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      buf += value ?? "";
      const lines = buf.split("\n");
      buf = done ? "" : lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const o = JSON.parse(line) as { row?: number; instruction?: string; input?: string; output?: string };
          if (typeof o.row === "number" && rows.has(o.row)) found.set(o.row, { instruction: o.instruction, input: o.input, output: o.output });
        } catch { /* skip a malformed line */ }
      }
      if (done || found.size >= rows.size) { await reader.cancel().catch(() => {}); break; }
    }
  }
  return found;
}

export function ReviewQueue({ items, initialLabels, demo, orgId, userId }:
  { items: ReviewItem[]; initialLabels: ReviewLabel[]; demo: boolean; orgId: string; userId: string }) {
  const [i, setI] = useState(0);
  const [score, setScore] = useState<number | null>(null);
  const [labels, setLabels] = useState<Record<string, Label>>(() =>
    Object.fromEntries(initialLabels.map((l) => [l.ref, { kind: l.kind, human: l.human, lorde: l.lorde }])));
  const [msg, setMsg] = useState<{ text: string; error?: boolean } | null>(null);
  const [saving, setSaving] = useState(false);
  const busy = useRef(false);
  const [texts, setTexts] = useState<Record<string, Text>>({});
  const [loading, setLoading] = useState(false), [textErr, setTextErr] = useState(""), [needKey, setNeedKey] = useState(false);
  const item = items[i];
  const done = items.length > 0 && items.every((x) => x.ref in labels);
  const stats = useMemo(() => agreement(Object.values(labels).map((l) => ({ human: l.human, lorde: l.lorde }))), [labels]);

  // The newest labels, for navigation right after a save (before React has re-rendered with them).
  const labelsRef = useRef(labels);
  labelsRef.current = labels;
  /** Move to the next row not yet labelled (wrapping round); stays put when none is left ("All caught up"). */
  const advance = useCallback((justLabelled?: string) => {
    setScore(null);
    const seen = new Set(Object.keys(labelsRef.current));
    if (justLabelled) seen.add(justLabelled);
    setI((x) => nextUnlabelled(items.map((it) => it.ref), seen, x) ?? x);
  }, [items]);
  const already = item ? labels[item.ref] : undefined;

  const save = useCallback(async () => {
    if (!item || score === null || busy.current) return;
    if (item.ref in labelsRef.current) return advance();  // saved before (reached with ←): never insert it twice
    busy.current = true; setSaving(true); setMsg(null);
    try {
      if (!demo) {
        const { supabaseBrowser } = await import("@/lib/supabase/client");
        const { error } = await supabaseBrowser().from("human_labels")
          .insert({ org_id: orgId, item_ref: item.ref, kind: item.kind, score, labeler: userId });
        if (error) return setMsg({ text: "Not saved: the server rejected the label. Try again.", error: true });
      }
      setLabels((l) => ({ ...l, [item.ref]: { kind: item.kind, human: score, lorde: item.lorde_score } }));
      setMsg({ text: demo ? "Labelled (demo: not saved)." : "Saved." });
      advance(item.ref);
    } catch {
      setMsg({ text: "Not saved: could not reach the server. Try again.", error: true });
    } finally {
      busy.current = false; setSaving(false);
    }
  }, [item, score, demo, orgId, userId, advance]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest("input, textarea, select, a, [contenteditable]")) return;
      const btn = t?.closest("button");
      if (e.key === "Enter") {
        // Enter on the score buttons or Save saves once (no extra click); on other buttons it does their own action.
        if (btn && !btn.hasAttribute("data-enter-saves")) return;
        e.preventDefault();
        if (!e.repeat) save();
        return;
      }
      if (btn && e.key === " ") return;
      if (/^[0-9]$/.test(e.key)) setScore(Number(e.key) / 10);
      else if (e.key === "p" || e.key === "P") setScore(1);
      else if (e.key === "s" || e.key === "S") advance();
      else if (e.key === "ArrowRight") { setScore(null); setI((x) => Math.min(items.length - 1, x + 1)); }
      else if (e.key === "ArrowLeft") { setScore(null); setI((x) => Math.max(0, x - 1)); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save, advance, items.length]);

  async function loadText() {
    if (!item?.job_id || loading) return;
    if (!getKey()) return setNeedKey(true);
    setLoading(true); setTextErr("");
    try {
      const sameJob = items.filter((x) => x.job_id === item.job_id && typeof x.row_idx === "number");
      const found = await loadRowText(item.job_id, new Set(sameJob.map((x) => x.row_idx!)));
      const next: Record<string, Text> = {};
      for (const x of sameJob) { const t = found.get(x.row_idx!); if (t) next[x.ref] = t; }
      setTexts((s) => ({ ...s, ...next }));
      if (!next[item.ref]) setTextErr("This row is not in the job's result files (they may have expired).");
    } catch (e) {
      setTextErr(`Could not load the row text: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }

  const t: Text = item ? { ...texts[item.ref] } : {};
  const instruction = item?.instruction ?? item?.question ?? t.instruction;
  const input = item?.input ?? item?.source_text ?? t.input;
  const output = item?.output ?? item?.response ?? item?.answer ?? t.output;
  const hasText = Boolean(instruction || input || output);
  const labelled = Object.entries(labels);

  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_300px]">
      {done || !item ? (
        <Card className="p-6">
          <p className="font-medium">All caught up</p>
          <p className="mt-2 text-sm text-muted">You have labelled every row in the queue. New rows appear after the next job that stores content.</p>
        </Card>
      ) : (
        <Card className="p-6">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="accent">{item.kind === "grounding" ? "Grounding" : "Reward"}</Badge>
            <Badge>{item.lang}</Badge>
            {item.job_id && <span className="font-mono text-[12px] text-muted">job {item.job_id.slice(0, 8)} · row {item.row_idx}</span>}
            <span className="tnum ml-auto text-[13px] text-muted">{i + 1} / {items.length}</span>
          </div>
          {hasText ? (
            <div className="mt-5 space-y-4 text-[15px] leading-relaxed" lang={item.lang !== "?" ? item.lang : undefined} dir={item.lang === "ar" ? "rtl" : undefined}>
              {item.kind === "grounding" ? (
                <>
                  <section><p className="font-mono text-[11px] uppercase text-muted">Source</p><p className="mt-1 whitespace-pre-wrap">{input ?? "–"}</p></section>
                  <section><p className="font-mono text-[11px] uppercase text-muted">Question</p><p className="mt-1 whitespace-pre-wrap">{instruction ?? "–"}</p></section>
                  <section><p className="font-mono text-[11px] uppercase text-muted">Answer</p><p className="mt-1 whitespace-pre-wrap font-medium">{output ?? "–"}</p></section>
                </>
              ) : (
                <>
                  <section><p className="font-mono text-[11px] uppercase text-muted">Instruction</p><p className="mt-1 whitespace-pre-wrap">{instruction ?? "–"}</p></section>
                  {input && <section><p className="font-mono text-[11px] uppercase text-muted">Input</p><p className="mt-1 whitespace-pre-wrap">{input}</p></section>}
                  <section><p className="font-mono text-[11px] uppercase text-muted">Response</p><p className="mt-1 whitespace-pre-wrap font-medium">{output ?? "–"}</p></section>
                </>
              )}
            </div>
          ) : (
            <div className="mt-5 rounded-xl bg-wash p-4 text-sm">
              <p className="text-muted">The row text is kept in the job&apos;s result files, not in the database. Load it with your API key (jobs:read).</p>
              <Button className="mt-3" size="sm" variant="secondary" onClick={loadText} disabled={loading}>{loading ? "Loading…" : "Load row text"}</Button>
              {needKey && !getKey() && <div className="mt-3"><ConnectKey need={["jobs:read"]} onChange={(c) => { if (c) { setNeedKey(false); } }} /></div>}
              {textErr && <p role="alert" className="mt-2 text-reject">{textErr}</p>}
            </div>
          )}
          <p className="mt-6 text-[13px] text-muted">
            {item.kind === "grounding"
              ? "Is every claim supported by the source? Outside knowledge counts as unsupported. Pass is 0.7 or higher."
              : "Helpfulness, honesty and instruction following. Pass is 0.7 or higher."}
          </p>
          <div role="radiogroup" aria-label="Score" className="mt-4 flex flex-wrap gap-1.5">
            {SCORES.map((s) => (
              <button key={s} role="radio" aria-checked={score === s} onClick={() => setScore(s)} data-enter-saves=""
                className={clsx("tnum h-10 w-12 rounded-xl border font-mono text-[13px] transition-colors",
                  score === s ? (s >= 0.7 ? "border-pass bg-pass text-white" : "border-reject bg-reject text-white")
                    : "border-line hover:bg-wash")}>
                {s.toFixed(1)}
              </button>
            ))}
          </div>
          <div className="mt-5 flex flex-wrap items-center gap-2">
            {already ? (
              <>
                <span role="status" className="text-[13px] text-muted">Already labelled: you gave {already.human.toFixed(1)}.</span>
                <Button variant="secondary" onClick={() => advance()}>Next unlabelled</Button>
              </>
            ) : (
              <>
                <Button onClick={save} disabled={score === null || saving} data-enter-saves="">{saving ? "Saving…" : "Save and next"}</Button>
                <Button variant="secondary" onClick={() => advance()}>Skip</Button>
              </>
            )}
            {msg && <span role={msg.error ? "alert" : "status"} className={`text-[13px] ${msg.error ? "text-reject" : "text-muted"}`}>{msg.text}</span>}
          </div>
          <p className="mt-4 font-mono text-[11px] text-faint">Keys: 0–9 = 0.0–0.9 · P = 1.0 · Enter save · S skip · ← → move</p>
        </Card>
      )}
      <aside className="space-y-4">
        <Card className="p-5">
          <p className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted">Agreement with Lorde</p>
          <p className="tnum mt-3 text-[34px] font-medium tracking-[-0.04em]">{pct(stats.agreement)}</p>
          <p className="tnum text-sm text-muted">κ {stats.kappa === null ? "–" : stats.kappa.toFixed(2)} · {stats.n} labeled</p>
          {!demo && <p className="mt-2 text-[12px] text-muted">Your labels, saved to your organization.</p>}
        </Card>
        <Card className="p-5 text-sm">
          <p className="font-medium">Labeled</p>
          <ul className="mt-3 max-h-80 space-y-2 overflow-y-auto">
            {labelled.slice(-50).reverse().map(([ref, l]) => {
              const agree = (l.human >= 0.7) === (l.lorde >= 0.7);
              return (
                <li key={ref} className="tnum flex items-center justify-between gap-2">
                  <span className="truncate text-muted">{ref.replace("judgment:", "#")} · {l.kind}</span>
                  <span className="font-mono text-[12px]">you {l.human.toFixed(1)} · Lorde {l.lorde.toFixed(2)} {agree ? "✓" : "✕"}</span>
                </li>
              );
            })}
            {!labelled.length && <li className="text-muted">Nothing yet. Lorde&apos;s score is hidden until you label.</li>}
          </ul>
        </Card>
      </aside>
    </div>
  );
}
