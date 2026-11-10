"use client";

import clsx from "clsx";
import { useState } from "react";

import { ConnectKey } from "@/components/console/connect-key";
import { Badge, Button, Card } from "@/components/ui";
import { curatorkitAction } from "@/lib/contract";
import { gatewayFetch } from "@/lib/gateway-client";
import type { Gate } from "@/lib/types";

const EX = {
  source_text: "The Niger River is the main river of West Africa, extending about 4,180 km. Its drainage basin is 2,117,700 km² in area. Its source is in the Guinea Highlands in south-eastern Guinea.",
  question: "How long is the Niger River?", answer: "It is about 6,650 km long.",
  instruction: "Explain in two sentences why the sky is blue.",
  response: "Sunlight scatters off air molecules, and shorter blue wavelengths scatter the most (Rayleigh scattering). So blue light reaches our eyes from every part of the sky.",
};

function Field({ id, label, value, set, rows = 3 }: { id: string; label: string; value: string; set: (v: string) => void; rows?: number }) {
  return (
    <div>
      <label htmlFor={id} className="text-sm font-medium">{label}</label>
      <textarea id={id} rows={rows} value={value} onChange={(e) => set(e.target.value)}
        className="mt-1.5 w-full resize-y rounded-xl border border-line bg-white px-3.5 py-2.5 text-[14px] leading-relaxed" />
    </div>
  );
}

function Verdict({ kind, raw, threshold, title, note }: { kind: Gate; raw: string; threshold: number; title: string; note?: string }) {
  const a = curatorkitAction(kind, raw, threshold);
  return (
    <Card className="flex flex-col p-5">
      <div className="flex items-center justify-between">
        <h3 className="font-medium">{title}</h3>
        {note && <Badge>{note}</Badge>}
      </div>
      <pre className="mt-3 max-h-60 overflow-auto rounded-xl bg-[#0d0e12] p-4 font-mono text-[12.5px] leading-6 whitespace-pre-wrap text-[#e6e8ec]">{raw}</pre>
      <div className={clsx("mt-4 rounded-xl border p-3.5 text-[14px]",
        a.decision === "pass" ? "border-[#bfe6da] bg-pass-wash" : "border-[#f6cfb3] bg-reject-wash")}>
        <p className="font-medium">{a.decision === "pass" ? "✓ CuratorKIT keeps the sample" : "✕ CuratorKIT drops the sample"}</p>
        <p className="mt-1 font-mono text-[12px] text-ink-2">
          {a.score !== null ? `score ${a.score.toFixed(2)} vs threshold ${threshold.toFixed(2)}` : a.detail}
          {a.reason ? ` · ${a.reason}` : ""}
        </p>
        {a.reason === "judge_error:JudgeParseError" && <p className="mt-1 text-[12px] text-muted">Not gate-usable: good data thrown away.</p>}
      </div>
    </Card>
  );
}

export function Playground({ demo, gateway = false }: { demo: boolean; gateway?: boolean }) {
  const [keyed, setKeyed] = useState(false);
  // Live judging needs a Gateway and the visitor's own key; otherwise the playground is simulated.
  const live = gateway && keyed;
  const [kind, setKind] = useState<Gate>("grounding");
  const [f, setF] = useState(EX);
  const [threshold, setThreshold] = useState(0.7);
  const [res, setRes] = useState<{ lorde: string; base?: string; simulated?: boolean; meta?: string } | null>(null);
  const [err, setErr] = useState(""), [busy, setBusy] = useState(false);
  const set = (k: keyof typeof EX) => (v: string) => setF({ ...f, [k]: v });

  async function judge() {
    setBusy(true); setErr("");
    const fields = kind === "grounding" ? { source_text: f.source_text, question: f.question, answer: f.answer }
      : { instruction: f.instruction, response: f.response };
    try {
      if (!live) {
        const r = await fetch("/api/judge", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ kind, ...fields }) });
        const j = await r.json();
        if (!r.ok) throw new Error(j.error?.message);
        setRes(j);
      } else {
        const r = await gatewayFetch(`/v1/judge/${kind}`, { method: "POST", body: JSON.stringify({ ...fields, threshold }) });
        const j = await r.json();
        if (!r.ok) throw new Error(j.error?.message ?? r.statusText);
        const { decision: _d, threshold: _t, model, template, latency_ms, request_id: _r, ...reply } = j;
        setRes({ lorde: JSON.stringify(reply, null, 2), meta: `${model} · ${template} · ${latency_ms} ms` });
      }
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <div className="space-y-6">
      {gateway && <ConnectKey onChange={setKeyed} />}
      <div className="grid gap-6 xl:grid-cols-2">
        <Card className="space-y-4 p-5">
          <div role="radiogroup" aria-label="Gate" className="inline-flex rounded-full border border-line p-1">
            {(["grounding", "reward"] as Gate[]).map((k) => (
              <button key={k} role="radio" aria-checked={kind === k} onClick={() => { setKind(k); setRes(null); }}
                className={clsx("rounded-full px-4 py-1.5 text-sm capitalize", kind === k ? "bg-ink text-white" : "text-muted hover:text-ink")}>{k}</button>
            ))}
          </div>
          {kind === "grounding" ? (
            <>
              <Field id="src" label="Source text" value={f.source_text} set={set("source_text")} rows={5} />
              <Field id="q" label="Question" value={f.question} set={set("question")} rows={1} />
              <Field id="a" label="Answer" value={f.answer} set={set("answer")} rows={2} />
            </>
          ) : (
            <>
              <Field id="ins" label="Instruction" value={f.instruction} set={set("instruction")} rows={2} />
              <Field id="res" label="Response" value={f.response} set={set("response")} rows={5} />
            </>
          )}
          <div className="flex flex-wrap items-center gap-4">
            <label htmlFor="thr" className="text-sm text-muted">Threshold <span className="tnum font-mono text-ink">{threshold.toFixed(2)}</span></label>
            <input id="thr" type="range" min={0.3} max={0.95} step={0.05} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))}
              className="w-40 accent-[var(--color-accent)]" />
            <Button onClick={judge} disabled={busy} className="ml-auto">{busy ? "Judging…" : "Judge"}</Button>
          </div>
          {err && <p role="alert" className="text-sm text-reject">{err}</p>}
        </Card>
        <div className="grid content-start gap-4">
          {!res && <Card className="p-8 text-center text-sm text-muted">Replies appear here, with CuratorKIT&apos;s decision for each.</Card>}
          {res && <Verdict kind={kind} raw={res.lorde} threshold={threshold} title="Lorde" note={res.simulated ? "Simulated" : res.meta} />}
          {res?.base && <Verdict kind={kind} raw={res.base} threshold={threshold} title="Untuned small model" note="Simulated" />}
        </div>
      </div>
      {!live && <p className="text-[12px] text-muted">Simulated: replies come from a transparent heuristic in the shape Lorde returns, not from the model.{gateway ? " Connect an API key above to judge with the live Gateway." : ""}</p>}
    </div>
  );
}
