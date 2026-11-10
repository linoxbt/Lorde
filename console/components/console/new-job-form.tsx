"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { ConnectKey } from "@/components/console/connect-key";
import { Button, Card } from "@/components/ui";
import { pct } from "@/lib/format";
import { gatewayFetch } from "@/lib/gateway-client";
import type { Dataset, Judgment } from "@/lib/types";

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
          <label htmlFor={`${id}-thr`} className="text-muted">Threshold</label>
          <span className="tnum font-mono">{thr.toFixed(2)}</span>
        </div>
        <input id={`${id}-thr`} type="range" min={0.3} max={0.95} step={0.05} value={thr} disabled={!on}
          onChange={(e) => setThr(Number(e.target.value))} className="mt-2 w-full accent-[var(--color-accent)]" />
        <p className="tnum mt-2 text-[13px] text-muted">Preview pass rate <span className="font-medium text-ink">{pct(rate)}</span></p>
      </div>
    </Card>
  );
}

export function NewJobForm({ datasets, sample, demo, preselect, sampleFrom }:
  { datasets: Dataset[]; sample: Judgment[]; demo: boolean; preselect?: string; sampleFrom?: string }) {
  const router = useRouter();
  const [ds, setDs] = useState(preselect ?? datasets[0]?.id ?? "");
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
    if (!g && !r) return setMsg("Enable at least one gate.");
    setBusy(true); setMsg("");
    const body = { upload_id: ds, gates: { grounding: g ? gt : null, reward: r ? rt : null }, options: { pii, dedup } };
    if (demo) { setBusy(false); return setMsg(`Demo: would POST /v1/jobs ${JSON.stringify(body)}`); }
    const res = await gatewayFetch("/v1/jobs", { method: "POST", body: JSON.stringify(body) });
    const j = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) return setMsg(j.error?.message ?? `Failed (${res.status})`);
    router.push(`/console/jobs/${j.id}`);
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_340px]">
      <div className="space-y-4">
        {!demo && <ConnectKey />}
        <Card className="p-5">
          <label htmlFor="ds" className="font-medium">Dataset</label>
          <select id="ds" value={ds} onChange={(e) => setDs(e.target.value)} className="mt-3 h-11 w-full rounded-xl border border-line bg-white px-3 text-[14px]">
            {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
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
            Estimated on {sample.length || "no"} judged rows{sampleFrom ? ` from your last job (${sampleFrom})` : ""}. The real pass rate depends on your data.
          </p>
          <Button className="mt-6 w-full" size="lg" onClick={submit} disabled={busy || !ds}>{busy ? "Starting…" : "Start job"}</Button>
          {msg && <p role="status" className="mt-3 break-words text-[12px] text-muted">{msg}</p>}
        </Card>
      </aside>
    </div>
  );
}
