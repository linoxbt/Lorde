"use client";

import { KeyRound } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui";
import { checkKey, getKey, setKey } from "@/lib/gateway-client";
import type { Scope } from "@/lib/scopes";

/** Connect the user's own API key for this browser tab (live mode only), after checking it with the Gateway. */
export function ConnectKey({ onChange, need = ["judge"] }: { onChange?: (connected: boolean) => void; need?: Scope[] }) {
  const [k, setK] = useState("");
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false), [err, setErr] = useState("");
  useEffect(() => { const c = Boolean(getKey()); setConnected(c); onChange?.(c); }, [onChange]);
  if (connected) {
    return (
      <div className="flex items-center gap-2 text-[13px] text-muted">
        <KeyRound className="size-3.5 text-pass" aria-hidden /> API key connected for this tab
        <button className="text-accent hover:underline" onClick={() => { setKey(""); setConnected(false); onChange?.(false); }}>Disconnect</button>
      </div>
    );
  }
  async function connect(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    const key = k.trim();
    if (!key.startsWith("lorde_sk_")) return setErr("API keys start with lorde_sk_.");
    setBusy(true); setErr("");
    try {
      const res = await checkKey(key, need);
      if (!res.ok) return setErr(res.message);
      setKey(key); setK(""); setConnected(true); onChange?.(true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div>
      <form className="flex flex-wrap items-center gap-2" onSubmit={connect}>
        <label htmlFor="ck" className="sr-only">API key</label>
        <input id="ck" type="password" autoComplete="off" placeholder="lorde_sk_…" value={k} onChange={(e) => setK(e.target.value)}
          className="h-9 w-64 rounded-full border border-line px-4 font-mono text-[13px]" />
        <Button size="sm" variant="secondary" type="submit" disabled={busy}>{busy ? "Checking…" : "Connect key"}</Button>
        <span className="text-[12px] text-muted">Kept in this tab only. Needs: {need.join(", ")}.</span>
      </form>
      {err && <p role="alert" className="mt-2 text-[13px] text-reject">{err}</p>}
    </div>
  );
}
