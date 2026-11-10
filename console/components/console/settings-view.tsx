"use client";

import { useState } from "react";

import { Table } from "@/components/console/bits";
import { Badge, Button, Card } from "@/components/ui";
import { ago } from "@/lib/format";
import type { Member, Org, Role } from "@/lib/types";

export function SettingsView({ org, members, role, demo }: { org: Org; members: Member[]; role: Role; demo: boolean }) {
  const admin = role === "owner" || role === "admin";
  const [name, setName] = useState(org.name);
  const [days, setDays] = useState(org.retention_days);
  const [store, setStore] = useState(org.store_content);
  const [msg, setMsg] = useState(""), [confirm, setConfirm] = useState("");

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (demo) return setMsg("Demo: settings are not saved.");
    const { supabaseBrowser } = await import("@/lib/supabase/client");
    const { error } = await supabaseBrowser().from("orgs").update({ name, retention_days: days, store_content: store }).eq("id", org.id);
    setMsg(error ? `Not saved: ${error.message}` : "Saved.");
  }

  return (
    <div className="space-y-8">
      <Card className="p-6">
        <h2 className="font-medium">Organization</h2>
        <form onSubmit={save} className="mt-5 grid gap-5 md:grid-cols-2">
          <div>
            <label htmlFor="on" className="text-sm font-medium">Name</label>
            <input id="on" value={name} disabled={!admin} onChange={(e) => setName(e.target.value)} className="mt-1.5 h-10 w-full rounded-xl border border-line px-3 text-[14px] disabled:bg-wash" />
          </div>
          <div>
            <label htmlFor="rd" className="text-sm font-medium">Dataset and result retention (days)</label>
            <input id="rd" type="number" min={1} max={365} value={days} disabled={!admin} onChange={(e) => setDays(Number(e.target.value))}
              className="tnum mt-1.5 h-10 w-32 rounded-xl border border-line px-3 text-[14px] disabled:bg-wash" />
            <p className="mt-1 text-[12px] text-muted">A daily job deletes expired files and records each deletion in the audit log.</p>
          </div>
          <label className="flex items-start gap-3 text-sm md:col-span-2">
            <input type="checkbox" checked={store} disabled={!admin} onChange={(e) => setStore(e.target.checked)} className="mt-0.5 size-4 accent-[var(--color-accent)]" />
            <span><span className="font-medium">Store judged content</span><br />
              <span className="text-muted">Off by default: Lorde keeps scores and decisions only. Turn on to review rows in the Console and join weekly drift checks.</span></span>
          </label>
          <div className="flex items-center gap-3 md:col-span-2">
            <Button type="submit" disabled={!admin}>Save</Button>
            {msg && <span role="status" className="text-sm text-muted">{msg}</span>}
          </div>
        </form>
      </Card>
      <section>
        <h2 className="mb-3 font-medium">Members</h2>
        <Table head={["Member", "Role", "Joined"]} minWidth={480}>
          {members.map((m) => (
            <tr key={m.user_id}>
              <td className="px-4 py-3">{m.email}</td>
              <td className="px-4 py-3"><Badge tone={m.role === "owner" ? "accent" : "neutral"}>{m.role}</Badge></td>
              <td className="px-4 py-3 text-muted">{ago(m.created_at)}</td>
            </tr>
          ))}
        </Table>
        <p className="mt-2 text-[12px] text-muted">Owners and admins manage keys and settings; members read everything in the organization.</p>
      </section>
      <Card className="border-[#f6cfb3] p-6">
        <h2 className="font-medium text-[#9a4300]">Delete organization data</h2>
        <p className="mt-2 max-w-2xl text-sm text-muted">Deletes every dataset, job result and human label for {org.name}, and records the deletion in the audit log within 7 days. This cannot be undone.</p>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <label htmlFor="cf" className="sr-only">Type the organization name to confirm</label>
          <input id="cf" value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder={`Type “${org.name}” to confirm`}
            className="h-10 w-72 rounded-xl border border-line px-3 text-[14px]" />
          <Button variant="danger" disabled={role !== "owner" || confirm !== org.name}
            onClick={() => setMsg(demo ? "Demo: nothing was deleted." : "Deletion requested; you will get an email when it completes.")}>
            Delete data
          </Button>
        </div>
      </Card>
    </div>
  );
}
