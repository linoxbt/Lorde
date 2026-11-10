"use client";

import { Download, XCircle } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui";
import { gatewayFetch } from "@/lib/gateway-client";
import type { JobStatus } from "@/lib/types";

export function JobActions({ id, status, demo }: { id: string; status: JobStatus; demo: boolean }) {
  const [msg, setMsg] = useState("");
  const cancel = async () => {
    if (demo) return setMsg("Demo: in a live workspace this cancels the job within one worker heartbeat.");
    const r = await gatewayFetch(`/v1/jobs/${id}/cancel`, { method: "POST" });
    setMsg(r.ok ? "Cancellation requested." : `Could not cancel (${r.status}).`);
  };
  const download = async () => {
    if (demo) return setMsg("Demo: in a live workspace this downloads passed.jsonl, rejected.jsonl, report.md and provenance.");
    const r = await gatewayFetch(`/v1/jobs/${id}/results`);
    if (!r.ok) return setMsg(`Results unavailable (${r.status}).`);
    const files: Record<string, string> = (await r.json()).files;
    Object.values(files).forEach((u) => window.open(u, "_blank", "noopener"));
  };
  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex gap-2">
        {(status === "queued" || status === "running") && <Button variant="secondary" size="sm" onClick={cancel}><XCircle className="size-3.5" />Cancel</Button>}
        {status === "succeeded" && <Button size="sm" onClick={download}><Download className="size-3.5" />Download results</Button>}
      </div>
      {msg && <p role="status" className="max-w-sm text-right text-[12px] text-muted">{msg}</p>}
    </div>
  );
}
