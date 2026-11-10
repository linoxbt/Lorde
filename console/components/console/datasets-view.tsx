"use client";

import { UploadCloud } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";

import { Table } from "@/components/console/bits";
import { ConnectKey } from "@/components/console/connect-key";
import { Badge, Card } from "@/components/ui";
import { ago, bytes, nf } from "@/lib/format";
import { gatewayFetch } from "@/lib/gateway-client";
import type { Dataset } from "@/lib/types";

const OK = /\.(jsonl|json|csv|parquet)$/i;

export function DatasetsView({ initial, demo }: { initial: Dataset[]; demo: boolean }) {
  const [rows, setRows] = useState(initial);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function upload(f: File) {
    if (!OK.test(f.name)) return setMsg("Upload a .jsonl, .csv or .parquet file.");
    if (f.size > 200 * 1024 * 1024) return setMsg("Uploads are limited to 200 MB.");
    setBusy(true); setMsg("");
    try {
      const fmt = f.name.split(".").pop()!.toLowerCase().replace("json", "jsonl").replace("jsonll", "jsonl") as Dataset["format"];
      let rowCount: number | null = null;
      if (fmt === "jsonl" && f.size < 50 * 1024 * 1024) rowCount = (await f.text()).split("\n").filter((l) => l.trim()).length;
      if (demo) {
        setRows([{ id: `local-${Date.now()}`, name: f.name, format: fmt, rows: rowCount, bytes: f.size,
          created_at: new Date().toISOString(), expires_at: null }, ...rows]);
        setMsg("Demo: the file stayed in your browser. In a live workspace it uploads to private storage.");
        return;
      }
      const r = await gatewayFetch("/v1/uploads", { method: "POST", body: JSON.stringify({ filename: f.name, bytes: f.size }) });
      const u = await r.json();
      if (!r.ok) throw new Error(u.error?.message ?? r.statusText);
      // Signed URLs are absolute for Supabase Storage, relative to the Gateway for its local storage backend.
      const target = u.upload_url.startsWith("http") ? u.upload_url : `${process.env.NEXT_PUBLIC_LORDE_API_URL ?? ""}${u.upload_url}`;
      const put = await fetch(target, { method: "PUT", body: f });
      if (!put.ok) throw new Error(`upload failed (${put.status})`);
      setRows([{ id: u.upload_id, name: f.name, format: u.format, rows: rowCount, bytes: f.size, created_at: new Date().toISOString(), expires_at: null }, ...rows]);
      setMsg("Uploaded. Start a job with it from New job.");
    } catch (e) {
      setMsg(`Upload failed: ${(e as Error).message}`);
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-6">
      {!demo && <ConnectKey />}
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
        {msg && <p role="status" className="mt-4 text-[13px] text-muted">{msg}</p>}
      </Card>
      <Table head={["Name", "Format", "Rows", "Size", "Uploaded", "Expires", ""]} minWidth={760}>
        {rows.map((d) => (
          <tr key={d.id} className="hover:bg-wash/60">
            <td className="max-w-[300px] truncate px-4 py-3 font-medium">{d.name}</td>
            <td className="px-4 py-3"><Badge>{d.format}</Badge></td>
            <td className="px-4 py-3">{d.rows !== null ? nf.format(d.rows) : "–"}</td>
            <td className="px-4 py-3">{bytes(d.bytes)}</td>
            <td className="px-4 py-3 text-muted">{ago(d.created_at)}</td>
            <td className="px-4 py-3 text-muted">{d.expires_at ? new Date(d.expires_at).toISOString().slice(0, 10) : "–"}</td>
            <td className="px-4 py-3 text-right"><Link href={`/console/jobs/new?dataset=${d.id}`} className="text-[13px] font-medium text-accent hover:underline">Gate it</Link></td>
          </tr>
        ))}
      </Table>
    </div>
  );
}
