"use client";

import { Check, Copy, Plus } from "lucide-react";
import { useState } from "react";

import { Table } from "@/components/console/bits";
import { Badge, Button, Card } from "@/components/ui";
import { ago } from "@/lib/format";
import type { ApiKeyRow } from "@/lib/types";

const SCOPES = [["judge", "Judge items"], ["jobs:read", "Read jobs"], ["jobs:write", "Create and cancel jobs"], ["usage:read", "Read usage"]] as const;

export function KeysView({ initial, canWrite }: { initial: ApiKeyRow[]; canWrite: boolean }) {
  const [rows, setRows] = useState(initial);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(""), [rpm, setRpm] = useState(600);
  const [scopes, setScopes] = useState<string[]>(["judge"]);
  const [created, setCreated] = useState<{ key: string; demo?: boolean } | null>(null);
  const [copied, setCopied] = useState(false), [err, setErr] = useState("");

  async function create(e: React.FormEvent) {
    e.preventDefault(); setErr("");
    const r = await fetch("/api/keys", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name, scopes, rpm }) });
    const j = await r.json();
    if (!r.ok) return setErr(j.error?.message ?? "Could not create the key");
    setRows([j.row, ...rows]); setCreated({ key: j.key, demo: j.demo }); setOpen(false); setName("");
  }
  async function revoke(id: string) {
    const r = await fetch(`/api/keys?id=${encodeURIComponent(id)}`, { method: "DELETE" });
    if (r.ok) setRows(rows.map((k) => (k.id === id ? { ...k, revoked_at: new Date().toISOString() } : k)));
  }

  return (
    <div className="space-y-6">
      {created && (
        <Card className="border-accent-soft bg-accent-wash p-5">
          <p className="font-medium">Copy your new key now. It will not be shown again.</p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <code className="break-all rounded-xl bg-white px-3 py-2 font-mono text-[13px]">{created.key}</code>
            <Button size="sm" variant="secondary" onClick={() => { navigator.clipboard?.writeText(created.key); setCopied(true); }}>
              {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}{copied ? "Copied" : "Copy"}
            </Button>
            <button className="text-[13px] text-muted hover:text-ink" onClick={() => setCreated(null)}>Done</button>
          </div>
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
              <input id="krpm" type="number" min={1} max={100000} value={rpm} onChange={(e) => setRpm(Number(e.target.value))}
                className="tnum mt-1.5 h-10 w-40 rounded-xl border border-line px-3 text-[14px]" />
            </div>
            <fieldset>
              <legend className="text-sm font-medium">Scopes</legend>
              <div className="mt-2 space-y-2">
                {SCOPES.map(([s, d]) => (
                  <label key={s} className="flex items-center gap-2.5 text-sm">
                    <input type="checkbox" checked={scopes.includes(s)} className="size-4 accent-[var(--color-accent)]"
                      onChange={(e) => setScopes(e.target.checked ? [...scopes, s] : scopes.filter((x) => x !== s))} />
                    <code className="font-mono text-[12px]">{s}</code><span className="text-muted">{d}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="flex items-center gap-2 md:col-span-2">
              <Button type="submit" disabled={!name || !scopes.length}>Create</Button>
              <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
              {err && <span role="alert" className="text-sm text-reject">{err}</span>}
            </div>
          </form>
        </Card>
      )}
      <Table head={["Name", "Prefix", "Scopes", "Limit", "Last used", "Status", ""]} minWidth={820}>
        {rows.map((k) => (
          <tr key={k.id} className="hover:bg-wash/60">
            <td className="px-4 py-3 font-medium">{k.name}</td>
            <td className="px-4 py-3 font-mono text-[13px]">{k.prefix}…</td>
            <td className="px-4 py-3"><div className="flex flex-wrap gap-1">{k.scopes.map((s) => <Badge key={s}>{s}</Badge>)}</div></td>
            <td className="px-4 py-3">{k.rpm}/min</td>
            <td className="px-4 py-3 text-muted">{ago(k.last_used_at)}</td>
            <td className="px-4 py-3">{k.revoked_at ? <Badge tone="reject">Revoked</Badge> : <Badge tone="pass">Active</Badge>}</td>
            <td className="px-4 py-3 text-right">
              {!k.revoked_at && canWrite && <button onClick={() => revoke(k.id)} className="text-[13px] font-medium text-reject hover:underline">Revoke</button>}
            </td>
          </tr>
        ))}
      </Table>
    </div>
  );
}
