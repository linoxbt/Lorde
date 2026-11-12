"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { ConnectKey } from "@/components/console/connect-key";
import { Button, Card } from "@/components/ui";
import { DATASET_STATE_LABEL, datasetState, readPending } from "@/lib/datasets";
import { pct } from "@/lib/format";
import { errorMessage, gatewayFetch, getKey } from "@/lib/gateway-client";
import type { Dataset, Judgment } from "@/lib/types";

type Basis = { rows: number; sameDataset: boolean; name: string; jobId: string } | null;

function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
}

function Gate({ id, label, desc, on, setOn, thr, setThr, rate }:
  { id: string; label: string; desc: string; on: boolean; setOn: (b: boolean) => void; thr: number; setThr: (n: number) => void; rate: number | null }) {
  return (
    <Card className={`p-5 ${on ? "" : "opacity-60"}`}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <label htmlFor={`${id}-on`} className="font-medium">{label}</label>
          <p className="mt-1 text-sm text-muted">{desc}</p>
        </div>
        <input id={`${id}-on`} type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} className="mt-1 size-5 accent-[var(--color-accent)]" />
      </div>
      <div className="mt-5">
        <div className="flex items-center justify-between text-sm">
          <label htmlFor={`${id}-thr`} className="text-muted">Threshold<span className="sr-only"> for the {label.toLowerCase()}</span></label>
          <span className="tnum font-mono">{thr.toFixed(2)}</span>
        </div>
        <input id={`${id}-thr`} type="range" min={0.3} max={0.95} step={0.05} value={thr} disabled={!on} aria-valuetext={thr.toFixed(2)}
          onChange={(e) => setThr(Number(e.target.value))} className="mt-2 w-full accent-[var(--color-accent)]" />
        <p className="tnum mt-2 text-[13px] text-muted">Preview pass rate <span className="font-medium text-ink">{pct(rate)}</span></p>
      </div>
    </Card>
  );
}

export function NewJobForm({ datasets, sample, demo, selected, basis, clock }:
  { datasets: Dataset[]; sample: Pick<Judgment, "gate" | "score">[]; demo: boolean; selected?: string; basis: Basis; clock?: number }) {
  const router = useRouter();
  const [ds, setDs] = useState(selected ?? "");
  const pick = (id: string) => { setDs(id); router.replace(`/console/jobs/new?dataset=${encodeURIComponent(id)}`, { scroll: false }); };
  // Expired datasets and this browser's unfinished uploads cannot start a job (lib/datasets.ts).
  const [pending, setPending] = useState<Record<string, number>>({});
  const [now, setNow] = useState(() => clock ?? Date.now());
  useEffect(() => { setPending(readPending(window.localStorage)); setNow(clock ?? Date.now()); }, [clock]);
  const states = useMemo(() => new Map(datasets.map((d) => [d.id, datasetState(d, now, pending[d.id])])), [datasets, now, pending]);
  const usable = (id: string) => states.get(id) === "ready";
  useEffect(() => {
    if (ds && !usable(ds)) setDs(datasets.find((d) => usable(d.id))?.id ?? "");
  }, [states]); // eslint-disable-line react-hooks/exhaustive-deps
  /** One Idempotency-Key per job request, reused when the same request is retried (a lost answer cannot start two jobs). */
  const idem = useRef<{ body: string; key: string } | null>(null);
  const [g, setG] = useState(true), [gt, setGt] = useState(0.7);
  const [r, setR] = useState(true), [rt, setRt] = useState(0.7);
  const [pii, setPii] = useState(false), [dedup, setDedup] = useState("exact");
  const [msg, setMsg] = useState(""), [busy, setBusy] = useState(false);

  const rate = (gate: "grounding" | "reward", thr: number) => {
    const xs = sample.filter((s) => s.gate === gate && s.score !== null);
    return xs.length ? xs.filter((s) => (s.score ?? 0) >= thr).length / xs.length : null;
  };
  const both = useMemo(() => {
    const a = g ? rate("grounding", gt) : 1, b = r ? rate("reward", rt) : 1;
    return a === null || b === null ? null : a * b;
  }, [g, gt, r, rt, sample]); // eslint-disable-line react-hooks/exhaustive-deps

  async function submit() {
    if (busy) return;
    if (!g && !r) return setMsg("Enable at least one gate.");
    if (!ds) return setMsg("Upload a dataset first.");
    if (!usable(ds)) return setMsg("This dataset cannot start a job: it has expired or its upload did not finish.");
    const body = { upload_id: ds, gates: { grounding: g ? gt : null, reward: r ? rt : null }, options: { pii, dedup } };
    if (demo) return setMsg(`Demo: would POST /v1/jobs ${JSON.stringify(body)}`);
    if (!getKey()) return setMsg("Connect an API key with the jobs:write scope first.");
    const json = JSON.stringify(body);
    if (idem.current?.body !== json) idem.current = { body: json, key: newIdempotencyKey() };
    setBusy(true); setMsg("");
    try {
      const res = await gatewayFetch("/v1/jobs", { method: "POST", body: json, headers: { "idempotency-key": idem.current.key } });
      if (!res.ok) return setMsg(`Could not start the job: ${await errorMessage(res)}`);
      const j = await res.json() as { id: string };
      router.push(`/console/jobs/${j.id}`);
    } catch {
      setMsg("Could not reach the Console. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_340px]">
      <div className="space-y-4">
        {!demo && <ConnectKey need={["jobs:write"]} />}
        <Card className="p-5">
          <label htmlFor="ds" className="font-medium">Dataset</label>
          {datasets.length ? (
            <select id="ds" value={ds} onChange={(e) => pick(e.target.value)} className="mt-3 h-11 w-full rounded-xl border border-line bg-white px-3 text-[14px]">
              {datasets.map((d) => {
                const st = states.get(d.id) ?? "ready";
                return <option key={d.id} value={d.id} disabled={st !== "ready"}>{st === "ready" ? d.name : `${d.name} (${DATASET_STATE_LABEL[st].toLowerCase()})`}</option>;
              })}
            </select>
          ) : (
            <p className="mt-3 text-sm text-muted">No datasets yet. <a href="/console/datasets" className="text-accent hover:underline">Upload one</a> first.</p>
          )}
        </Card>
        <div className="grid gap-4 md:grid-cols-2">
          <Gate id="g" label="Grounding gate" desc="Is every claim supported by the row's input (source)?" on={g} setOn={setG} thr={gt} setThr={setGt} rate={g ? rate("grounding", gt) : null} />
          <Gate id="r" label="Reward gate" desc="Helpfulness, honesty and instruction following." on={r} setOn={setR} thr={rt} setThr={setRt} rate={r ? rate("reward", rt) : null} />
        </div>
        <Card className="grid gap-4 p-5 sm:grid-cols-2">
          <label className="flex items-start gap-3 text-sm">
            <input type="checkbox" checked={pii} onChange={(e) => setPii(e.target.checked)} className="mt-0.5 size-4 accent-[var(--color-accent)]" />
            <span><span className="font-medium">Pseudonymize PII</span><br /><span className="text-muted">Replace names, emails and numbers with consistent fakes before judging.</span></span>
          </label>
          <div className="text-sm">
            <label htmlFor="dedup" className="font-medium">Deduplication</label>
            <select id="dedup" value={dedup} onChange={(e) => setDedup(e.target.value)} className="mt-2 h-10 w-full rounded-xl border border-line bg-white px-3">
              <option value="exact">Exact</option><option value="minhash">Near-duplicates (MinHash)</option><option value="none">None</option>
            </select>
          </div>
        </Card>
      </div>
      <aside>
        <Card className="sticky top-24 p-5">
          <p className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted">Preview</p>
          <p className="tnum mt-3 text-[40px] font-medium tracking-[-0.04em]">{pct(both)}</p>
          <p className="text-sm text-muted">of rows would pass both gates</p>
          <p className="mt-4 text-[12px] leading-relaxed text-muted">
            {basis
              ? <>Estimated on {basis.rows} judgments from {basis.sameDataset ? "the most recent job on this dataset" : <>your most recent job (on {basis.name}), not on this dataset</>}. The real pass rate depends on your data.</>
              : "No finished job yet to estimate from: the preview appears after your first job."}
          </p>
          <Button className="mt-6 w-full" size="lg" onClick={submit} disabled={busy || !ds || !usable(ds)}>{busy ? "Starting…" : "Start job"}</Button>
          {msg && <p role="status" className="mt-3 break-words text-[12px] text-muted">{msg}</p>}
        </Card>
      </aside>
    </div>
  );
}
