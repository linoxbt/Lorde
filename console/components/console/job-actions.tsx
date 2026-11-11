"use client";

import { Download, XCircle } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ConnectKey } from "@/components/console/connect-key";
import { Button } from "@/components/ui";
import { errorMessage, gatewayFetch, getKey } from "@/lib/gateway-client";
import type { JobStatus } from "@/lib/types";

const LABELS: Record<string, string> = {
  passed: "passed.jsonl", rejected: "rejected.jsonl", report: "report.md", manifest: "manifest.json",
  lexsi_provenance: "lexsi_provenance.json", judgments: "judgments.jsonl", summary: "summary.json",
};

export function JobActions({ id, status, demo, cancelRequested }: { id: string; status: JobStatus; demo: boolean; cancelRequested?: boolean }) {
  const router = useRouter();
  const [msg, setMsg] = useState<{ text: string; error?: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [files, setFiles] = useState<Record<string, string> | null>(null);
  const [needKey, setNeedKey] = useState(false);

  async function cancel() {
    if (demo) return setMsg({ text: "Demo: in a live workspace this cancels the job within one worker heartbeat." });
    if (!getKey()) return setNeedKey(true);
    setBusy(true); setMsg(null);
    try {
      const r = await gatewayFetch(`/v1/jobs/${id}/cancel`, { method: "POST" });
      if (!r.ok) return setMsg({ text: `Could not cancel: ${await errorMessage(r)}`, error: true });
      setMsg({ text: "Cancellation requested. The job stops within one worker heartbeat." });
      router.refresh();
    } catch {
      setMsg({ text: "Could not reach the Console. Try again.", error: true });
    } finally {
      setBusy(false);
    }
  }
  async function download() {
    if (demo) return setMsg({ text: "Demo: in a live workspace this lists passed.jsonl, rejected.jsonl, report.md and provenance." });
    if (!getKey()) return setNeedKey(true);
    setBusy(true); setMsg(null);
    try {
      const r = await gatewayFetch(`/v1/jobs/${id}/results`);
      if (!r.ok) return setMsg({ text: `Results unavailable: ${await errorMessage(r)}`, error: true });
      const f = ((await r.json()) as { files?: Record<string, string> }).files ?? {};
      if (!Object.keys(f).length) return setMsg({ text: "This job has no result files (they may have been deleted after the retention period).", error: true });
      setFiles(f);
    } catch {
      setMsg({ text: "Could not reach the Console. Try again.", error: true });
    } finally {
      setBusy(false);
    }
  }
  const active = status === "queued" || status === "running";
  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex gap-2">
        {active && (
          <Button variant="secondary" size="sm" onClick={cancel} disabled={busy || cancelRequested}>
            <XCircle className="size-3.5" />{cancelRequested ? "Cancelling…" : "Cancel"}
          </Button>
        )}
        {status === "succeeded" && <Button size="sm" onClick={download} disabled={busy}><Download className="size-3.5" />{busy ? "Fetching…" : "Download results"}</Button>}
      </div>
      {needKey && !getKey() && <ConnectKey need={active ? ["jobs:write"] : ["jobs:read"]} onChange={(c) => c && setNeedKey(false)} />}
      {files && (
        <ul aria-label="Result files" className="space-y-1 text-right text-[13px]">
          {Object.entries(files).map(([name, url]) => (
            <li key={name}><a href={url} target="_blank" rel="noopener noreferrer" className="font-mono text-accent hover:underline">{LABELS[name] ?? name}</a></li>
          ))}
          <li className="text-[12px] text-muted">Links expire in 15 minutes.</li>
        </ul>
      )}
      {msg && <p role={msg.error ? "alert" : "status"} className={`max-w-sm text-right text-[12px] ${msg.error ? "text-reject" : "text-muted"}`}>{msg.text}</p>}
    </div>
  );
}
