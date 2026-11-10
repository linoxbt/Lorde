"use client";

import { KeyRound } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui";
import { getKey, setKey } from "@/lib/gateway-client";

/** Connect the user's own API key for this browser tab (live mode only). */
export function ConnectKey({ onChange }: { onChange?: (connected: boolean) => void }) {
  const [k, setK] = useState("");
  const [connected, setConnected] = useState(false);
  useEffect(() => { const c = Boolean(getKey()); setConnected(c); onChange?.(c); }, [onChange]);
  if (connected) {
    return (
      <div className="flex items-center gap-2 text-[13px] text-muted">
        <KeyRound className="size-3.5 text-pass" aria-hidden /> API key connected for this tab
        <button className="text-accent hover:underline" onClick={() => { setKey(""); setConnected(false); onChange?.(false); }}>Disconnect</button>
      </div>
    );
  }
  return (
    <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (k.startsWith("lorde_sk_")) { setKey(k); setConnected(true); onChange?.(true); } }}>
      <label htmlFor="ck" className="sr-only">API key</label>
      <input id="ck" type="password" autoComplete="off" placeholder="lorde_sk_…" value={k} onChange={(e) => setK(e.target.value)}
        className="h-9 w-64 rounded-full border border-line px-4 font-mono text-[13px]" />
      <Button size="sm" variant="secondary" type="submit">Connect key</Button>
      <span className="text-[12px] text-muted">Kept in this tab only.</span>
    </form>
  );
}
