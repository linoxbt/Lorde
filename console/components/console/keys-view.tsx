"use client";

import { Check, Copy, Plus } from "lucide-react";
import { useRef, useState } from "react";

import { Table } from "@/components/console/bits";
import { Badge, Button, Card } from "@/components/ui";
import { ago } from "@/lib/format";
import { DEFAULT_SCOPES, MAX_RPM, SCOPE_LABELS, SCOPES, type Scope } from "@/lib/scopes";
import type { ApiKeyRow } from "@/lib/types";

async function message(r: Response, fallback: string) {
  const j = await r.json().catch(() => null) as { error?: { message?: string } } | null;
  return j?.error?.message || fallback;
}

export function KeysView({ initial, total, canWrite }: { initial: ApiKeyRow[]; total: number; canWrite: boolean }) {
  const [rows, setRows] = useState(initial);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(""), [rpm, setRpm] = useState(600);
  const [scopes, setScopes] = useState<Scope[]>(DEFAULT_SCOPES);
  const [created, setCreated] = useState<{ key: string; demo?: boolean } | null>(null);
  const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle"), [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const creating = useRef(false);
  const [confirming, setConfirming] = useState<string | null>(null), [revoking, setRevoking] = useState<string | null>(null);
  const [rowErr, setRowErr] = useState("");

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (creating.current) return;  // one key per click: a second plaintext key would be lost
    creating.current = true; setBusy(true); setErr("");
    try {
      const r = await fetch("/api/keys", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name, scopes, rpm }) });
      if (!r.ok) return setErr(await message(r, "Could not create the key."));
      const j = await r.json();
      setRows((rs) => [j.row, ...rs]); setCreated({ key: j.key, demo: j.demo }); setCopy("idle"); setOpen(false); setName("");
    } catch {
      setErr("Could not reach the Console. Check your connection and try again.");
    } finally {
      creating.current = false; setBusy(false);
    }
  }
  async function revoke(id: string) {
    setRevoking(id); setRowErr("");
    try {
      const r = await fetch(`/api/keys?id=${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!r.ok) return setRowErr(await message(r, "Could not revoke the key."));
      const j = await r.json().catch(() => ({}));
      setRows((rs) => rs.map((k) => (k.id === id ? { ...k, revoked_at: j.revoked_at ?? new Date().toISOString() } : k)));
    } catch {
      setRowErr("Could not reach the Console. Check your connection and try again.");
    } finally {
      setRevoking(null); setConfirming(null);
    }
  }
  async function copyKey(key: string) {
    try {
      await navigator.clipboard.writeText(key);
      setCopy("copied");
    } catch {
      setCopy("failed");
    }
  }

  return (
    <div className="space-y-6">
      {created && (
        <Card className="border-accent-soft bg-accent-wash p-5">
          <p className="font-medium">Copy your new key now. It will not be shown again.</p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <code className="break-all rounded-xl bg-white px-3 py-2 font-mono text-[13px]">{created.key}</code>
            <Button size="sm" variant="secondary" onClick={() => copyKey(created.key)}>
              {copy === "copied" ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}{copy === "copied" ? "Copied" : "Copy"}
            </Button>
            <button className="text-[13px] text-muted hover:text-ink" onClick={() => setCreated(null)}>Done</button>
          </div>
          {copy === "failed" && <p role="alert" className="mt-2 text-[12px] text-reject">Copying is blocked in this browser: select the key and copy it manually.</p>}
          {created.demo && <p className="mt-2 text-[12px] text-muted">Demo: this key is not stored anywhere and will not authenticate.</p>}
        </Card>
      )}
      <div className="flex justify-end">
        <Button onClick={() => setOpen(!open)} disabled={!canWrite} title={canWrite ? undefined : "Owners and admins create keys"}>
          <Plus className="size-4" />Create key
        </Button>
      </div>
      {open && (
        <Card className="p-5">
          <form onSubmit={create} className="grid gap-5 md:grid-cols-2">
            <div>
              <label htmlFor="kn" className="text-sm font-medium">Name</label>
              <input id="kn" required maxLength={60} value={name} onChange={(e) => setName(e.target.value)} placeholder="ci-pipeline"
                className="mt-1.5 h-10 w-full rounded-xl border border-line px-3 text-[14px]" />
              <label htmlFor="krpm" className="mt-4 block text-sm font-medium">Rate limit (judgments per minute)</label>
              <input id="krpm" type="number" min={1} max={MAX_RPM} value={rpm} onChange={(e) => setRpm(Number(e.target.value))}
                className="tnum mt-1.5 h-10 w-40 rounded-xl border border-line px-3 text-[14px]" />
              <p className="mt-1 text-[12px] text-muted">Up to {MAX_RPM.toLocaleString("en-US")} per minute.</p>
            </div>
            <fieldset>
              <legend className="text-sm font-medium">Scopes</legend>
              <p className="mt-1 text-[12px] text-muted">All are on by default so the Console&apos;s screens work with this key; narrow them for CI or scripts.</p>
              <div className="mt-2 space-y-2">
                {SCOPES.map((s) => (
                  <label key={s} className="flex items-center gap-2.5 text-sm">
                    <input type="checkbox" checked={scopes.includes(s)} className="size-4 accent-[var(--color-accent)]"
                      onChange={(e) => setScopes(e.target.checked ? [...scopes, s] : scopes.filter((x) => x !== s))} />
                    <code className="font-mono text-[12px]">{s}</code><span className="text-muted">{SCOPE_LABELS[s]}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="flex items-center gap-2 md:col-span-2">
              <Button type="submit" disabled={busy || !name.trim() || !scopes.length || rpm < 1 || rpm > MAX_RPM}>{busy ? "Creating…" : "Create"}</Button>
              <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
              {err && <span role="alert" className="text-sm text-reject">{err}</span>}
            </div>
          </form>
        </Card>
      )}
      {rowErr && <p role="alert" className="text-sm text-reject">{rowErr}</p>}
      <Table head={["Name", "Prefix", "Scopes", "Limit", "Last used", "Status", ""]} minWidth={820}>
        {rows.map((k) => (
          <tr key={k.id} className="hover:bg-wash/60">
            <td className="px-4 py-3 font-medium">{k.name}</td>
            <td className="px-4 py-3 font-mono text-[13px]">{k.prefix}…</td>
            <td className="px-4 py-3"><div className="flex flex-wrap gap-1">{k.scopes.map((s) => <Badge key={s}>{s}</Badge>)}</div></td>
            <td className="px-4 py-3">{k.rpm}/min</td>
            <td className="px-4 py-3 text-muted">{ago(k.last_used_at)}</td>
            <td className="px-4 py-3">{k.revoked_at ? <Badge tone="reject">Revoked</Badge> : <Badge tone="pass">Active</Badge>}</td>
            <td className="whitespace-nowrap px-4 py-3 text-right">
              {!k.revoked_at && canWrite && (confirming === k.id ? (
                <span className="inline-flex items-center gap-3 text-[13px]">
                  <span className="text-muted">Revoke {k.name}? Apps using it stop working.</span>
                  <button onClick={() => revoke(k.id)} disabled={revoking === k.id} className="font-medium text-reject hover:underline disabled:opacity-50">
                    {revoking === k.id ? "Revoking…" : "Yes, revoke"}
                  </button>
                  <button onClick={() => setConfirming(null)} className="text-muted hover:text-ink">Keep</button>
                </span>
              ) : (
                <button onClick={() => setConfirming(k.id)} className="text-[13px] font-medium text-reject hover:underline">Revoke</button>
              ))}
            </td>
          </tr>
        ))}
      </Table>
      {total > rows.length && <p className="text-[12px] text-muted">Showing the {rows.length} newest of {total} keys.</p>}
    </div>
  );
}
