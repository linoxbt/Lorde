"use client";

import { UploadCloud } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Table } from "@/components/console/bits";
import { ConnectKey } from "@/components/console/connect-key";
import { Badge, Card } from "@/components/ui";
import { DATASET_STATE_LABEL, type DatasetState, datasetState, readPending, writePending } from "@/lib/datasets";
import { ago, bytes, nf } from "@/lib/format";
import { errorMessage, gatewayFetch, getKey } from "@/lib/gateway-client";
import type { Dataset } from "@/lib/types";

const OK = /\.(jsonl|json|csv|parquet)$/i;
const MAX_BYTES = 200 * 1024 * 1024;
type Upload = { upload_id: string; upload_url: string; method?: string; headers?: Record<string, string>; format: Dataset["format"] };
const TONE: Record<DatasetState, "pass" | "accent" | "reject" | "neutral"> = { ready: "pass", uploading: "accent", incomplete: "reject", expired: "neutral" };

/**
 * Upload a dataset: the Gateway's POST /v1/uploads creates the dataset row and returns a signed URL plus the method
 * and headers to use; the browser sends the file there directly. The Console never writes dataset rows itself.
 */
/** `clock`: a fixed "now" (the demo workspace's), else the real time. */
export function DatasetsView({ rows, total, demo, apiBase, clock }: { rows: Dataset[]; total: number; demo: boolean; apiBase: string; clock?: number }) {
  const router = useRouter();
  const [local, setLocal] = useState<Dataset[]>([]);
  const [msg, setMsg] = useState<{ text: string; error?: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  // This browser's unfinished uploads (lib/datasets.ts): read after mount, so the server render stays deterministic.
  const [pending, setPending] = useState<Record<string, number>>({});
  const [now, setNow] = useState(() => clock ?? Date.now());
  useEffect(() => { setPending(readPending(window.localStorage)); setNow(clock ?? Date.now()); }, [rows, clock]);
  const track = (id: string, v: number | null) => {
    const p = readPending(window.localStorage);
    if (v === null) delete p[id]; else p[id] = v;
    writePending(window.localStorage, p);
    setPending(p);
  };

  async function upload(f: File) {
    if (busy) return;
    if (!OK.test(f.name)) return setMsg({ text: "Upload a .jsonl, .csv or .parquet file.", error: true });
    if (f.size > MAX_BYTES) return setMsg({ text: "Uploads are limited to 200 MB.", error: true });
    if (f.size === 0) return setMsg({ text: "That file is empty.", error: true });
    const fmt = (/\.parquet$/i.test(f.name) ? "parquet" : /\.csv$/i.test(f.name) ? "csv" : "jsonl") as Dataset["format"];
    if (demo) {
      setLocal((l) => [{ id: `local-${Date.now()}`, name: f.name, format: fmt, rows: null, bytes: f.size,
        created_at: new Date().toISOString(), expires_at: null }, ...l]);
      return setMsg({ text: "Demo: the file stayed in your browser. In a live workspace it uploads to private storage." });
    }
    if (!getKey()) return setMsg({ text: "Connect an API key with the jobs:write scope first.", error: true });
    setBusy(true); setMsg({ text: `Uploading ${f.name}…` });
    try {
      const r = await gatewayFetch("/v1/uploads", { method: "POST", body: JSON.stringify({ filename: f.name, bytes: f.size }) });
      if (!r.ok) return setMsg({ text: `Upload not started: ${await errorMessage(r)}`, error: true });
      const u = await r.json() as Upload;
      track(u.upload_id, Date.now());  // the dataset row exists from here on; it is usable once the file has arrived
      // Signed URLs are absolute for Supabase Storage; the Gateway's local storage may return a path on the Gateway.
      let target: string;
      try {
        target = new URL(u.upload_url, apiBase || undefined).toString();
      } catch {
        return setMsg({ text: "Upload not started: the Gateway's public URL is not configured for this Console.", error: true });
      }
      let put: Response;
      try {
        put = await fetch(target, { method: u.method || "PUT", headers: u.headers ?? {}, body: f });
      } catch {
        track(u.upload_id, -Date.now());
        return setMsg({ text: "Upload failed: the storage server could not be reached. Try again.", error: true });
      }
      if (!put.ok) {
        track(u.upload_id, -Date.now());
        const reason = put.status === 413 ? "the file is larger than the server accepts" : put.status === 404 || put.status === 403
          ? "the upload link expired or was rejected" : `the storage server answered ${put.status}`;
        return setMsg({ text: `Upload failed: ${reason}. Try again.`, error: true });
      }
      track(u.upload_id, null);
      setMsg({ text: `Uploaded ${f.name}. Start a job with it from New job.` });
      router.refresh();
    } catch {
      setMsg({ text: "Upload failed: could not reach the Console. Check your connection and try again.", error: true });
    } finally {
      setBusy(false);
    }
  }

  const all = [...local, ...rows];
  return (
    <div className="space-y-6">
      {!demo && <ConnectKey need={["jobs:write"]} />}
      <Card
        onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) upload(f); }}
        className={`flex flex-col items-center border-dashed px-6 py-12 text-center transition-colors ${over ? "border-accent bg-accent-wash" : ""}`}>
        <UploadCloud className="size-8 text-accent" aria-hidden />
        <p className="mt-3 font-medium">Drop a dataset here</p>
        <p className="mt-1 text-sm text-muted">Rows need instruction/output fields; add input (the source) for the grounding gate.</p>
        <button type="button" disabled={busy} onClick={() => input.current?.click()}
          className="mt-5 inline-flex h-10 items-center rounded-full bg-accent px-5 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50">
          {busy ? "Uploading…" : "Choose a file"}
        </button>
        <input ref={input} type="file" accept=".jsonl,.json,.csv,.parquet" className="sr-only" aria-label="Choose a dataset file"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
        {msg && <p role={msg.error ? "alert" : "status"} className={`mt-4 text-[13px] ${msg.error ? "text-reject" : "text-muted"}`}>{msg.text}</p>}
      </Card>
      {all.length === 0 ? (
        <p className="text-sm text-muted">No datasets yet.</p>
      ) : (
        <Table head={["Name", "Status", "Format", "Rows", "Size", "Uploaded", "Expires", ""]} minWidth={860}>
          {all.map((d) => {
            const st = datasetState(d, now, pending[d.id]);
            return (
            <tr key={d.id} className="hover:bg-wash/60">
              <td className="max-w-[300px] truncate px-4 py-3 font-medium">{d.name}</td>
              <td className="px-4 py-3"><Badge tone={TONE[st]}>{DATASET_STATE_LABEL[st]}</Badge></td>
              <td className="px-4 py-3"><Badge>{d.format}</Badge></td>
              <td className="px-4 py-3">{d.rows !== null ? nf.format(d.rows) : "–"}</td>
              <td className="px-4 py-3">{bytes(d.bytes)}</td>
              <td className="px-4 py-3 text-muted">{ago(d.created_at)}</td>
              <td className="px-4 py-3 text-muted">{d.expires_at ? new Date(d.expires_at).toISOString().slice(0, 10) : "–"}</td>
              <td className="px-4 py-3 text-right">
                {st === "ready"
                  ? <Link href={`/console/jobs/new?dataset=${d.id}`} className="text-[13px] font-medium text-accent hover:underline">Gate it<span className="sr-only">: {d.name}</span></Link>
                  : <span className="text-[13px] text-faint">{st === "expired" ? "Deleted soon" : st === "uploading" ? "Waiting for the file" : "Upload it again"}</span>}
              </td>
            </tr>
            );
          })}
        </Table>
      )}
      {total > rows.length && <p className="text-[12px] text-muted">Showing the {rows.length} newest of {total} datasets.</p>}
      {!demo && <p className="text-[12px] text-muted">Row counts are not recorded at upload; each job reports the rows it read. Expired datasets wait for the retention purge, and uploads whose file did not finish uploading cannot start a job.</p>}
    </div>
  );
}
