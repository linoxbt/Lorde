"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Table } from "@/components/console/bits";
import { Badge, Button, Card } from "@/components/ui";
import { ago } from "@/lib/format";
import type { Member, Org, Role } from "@/lib/types";

type Deleted = { judgments: number; human_labels: number; datasets: number; job_results: number; jobs_cancelled: number; jobs_stopping: number };
type Msg = { text: string; error?: boolean } | null;
const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

export function SettingsView({ org, members, role, meId, demo }: { org: Org; members: Member[]; role: Role; meId: string; demo: boolean }) {
  const router = useRouter();
  const admin = role === "owner" || role === "admin";
  const [name, setName] = useState(org.name);
  const [days, setDays] = useState(org.retention_days);
  const [store, setStore] = useState(org.store_content);
  const [msg, setMsg] = useState<Msg>(null), [saving, setSaving] = useState(false);
  const [confirm, setConfirm] = useState(""), [delMsg, setDelMsg] = useState<Msg>(null), [deleting, setDeleting] = useState(false);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (saving) return;
    const clean = name.trim();
    if (!clean) return setMsg({ text: "The organization needs a name.", error: true });
    if (clean.length > 100) return setMsg({ text: "Use at most 100 characters for the name.", error: true });
    if (!Number.isInteger(days) || days < 1 || days > 365) return setMsg({ text: "Retention must be between 1 and 365 days.", error: true });
    if (demo) return setMsg({ text: "Demo: settings are not saved." });
    setSaving(true); setMsg(null);
    try {
      const { supabaseBrowser } = await import("@/lib/supabase/client");
      const { data, error } = await supabaseBrowser().from("orgs").update({ name: clean, retention_days: days, store_content: store })
        .eq("id", org.id).select("id");
      if (error) return setMsg({ text: "Not saved: the server rejected the change. Try again.", error: true });
      if (!data?.length) return setMsg({ text: "Not saved: only owners and admins can change these settings.", error: true });
      setName(clean);
      setMsg({ text: "Saved." });
      router.refresh();  // the sidebar shows the organization name
    } catch {
      setMsg({ text: "Not saved: could not reach the server. Try again.", error: true });
    } finally {
      setSaving(false);
    }
  }

  async function deleteData() {
    if (deleting || role !== "owner" || confirm !== org.name) return;
    if (demo) return setDelMsg({ text: "Demo: nothing was deleted." });
    setDeleting(true); setDelMsg(null);
    try {
      const { supabaseBrowser } = await import("@/lib/supabase/client");
      const { data, error } = await supabaseBrowser().rpc("request_org_data_deletion", { o: org.id });
      if (error) {
        return setDelMsg({ text: error.code === "42501" ? "Only an owner can delete the organization's data." : "Nothing was deleted: the request failed. Try again.", error: true });
      }
      const d = data as Deleted;
      const stopped = d.jobs_cancelled + d.jobs_stopping;
      setDelMsg({ text: `Deleted ${plural(d.judgments, "judgment")} and ${plural(d.human_labels, "human label")} now${stopped ? `, and cancelled ${plural(stopped, "job")}` : ""}. `
        + `${plural(d.datasets, "dataset")} and ${plural(d.job_results, "job's result files", "jobs' result files")} are marked expired: the retention job removes the files within 6 hours. `
        + "Usage records are kept for billing. The deletion is recorded in the audit log." });
      setConfirm("");
      router.refresh();
    } catch {
      setDelMsg({ text: "Nothing was deleted: could not reach the server. Try again.", error: true });
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="space-y-8">
      <Card className="p-6">
        <h2 className="font-medium">Organization</h2>
        <form onSubmit={save} className="mt-5 grid gap-5 md:grid-cols-2">
          <div>
            <label htmlFor="on" className="text-sm font-medium">Name</label>
            <input id="on" value={name} disabled={!admin} required maxLength={100} onChange={(e) => setName(e.target.value)} className="mt-1.5 h-10 w-full rounded-xl border border-line px-3 text-[14px] disabled:bg-wash" />
          </div>
          <div>
            <label htmlFor="rd" className="text-sm font-medium">Dataset and result retention (days)</label>
            <input id="rd" type="number" min={1} max={365} value={days} disabled={!admin} onChange={(e) => setDays(Number(e.target.value))}
              className="tnum mt-1.5 h-10 w-32 rounded-xl border border-line px-3 text-[14px] disabled:bg-wash" />
            <p className="mt-1 text-[12px] text-muted">A retention job runs every 6 hours, deletes expired files and records each deletion in the audit log.</p>
          </div>
          <label className="flex items-start gap-3 text-sm md:col-span-2">
            <input type="checkbox" checked={store} disabled={!admin} onChange={(e) => setStore(e.target.checked)} className="mt-0.5 size-4 accent-[var(--color-accent)]" />
            <span><span className="font-medium">Store judged content</span><br />
              <span className="text-muted">Off by default: Lorde keeps scores and decisions only. Turn on to review rows from new jobs in the Console.</span></span>
          </label>
          <div className="flex items-center gap-3 md:col-span-2">
            <Button type="submit" disabled={!admin || saving}>{saving ? "Saving…" : "Save"}</Button>
            {!admin && <span className="text-sm text-muted">Only owners and admins can change these settings.</span>}
            {msg && <span role={msg.error ? "alert" : "status"} className={`text-sm ${msg.error ? "text-reject" : "text-muted"}`}>{msg.text}</span>}
          </div>
        </form>
      </Card>
      <section>
        <h2 className="mb-3 font-medium">Members</h2>
        <Table head={["Member", "Role", "Joined"]} minWidth={480}>
          {members.map((m) => (
            <tr key={m.user_id}>
              <td className="px-4 py-3">{m.email || m.user_id.slice(0, 8)}{m.user_id === meId && <span className="ml-2 text-[12px] text-muted">(you)</span>}</td>
              <td className="px-4 py-3"><Badge tone={m.role === "owner" ? "accent" : "neutral"}>{m.role}</Badge></td>
              <td className="px-4 py-3 text-muted">{ago(m.created_at)}</td>
            </tr>
          ))}
        </Table>
        <p className="mt-2 text-[12px] text-muted">
          Owners and admins manage keys and settings; members read everything in the organization. Inviting members and
          changing roles is not available in the Console yet.
        </p>
      </section>
      <Card className="border-[#f6cfb3] p-6">
        <h2 className="font-medium text-[#9a4300]">Delete organization data</h2>
        <p className="mt-2 max-w-2xl text-sm text-muted">
          Deletes every judgment and human label for {org.name} immediately, cancels queued and running jobs, and expires every
          dataset and job result file; the retention job removes those files within 6 hours. Usage records (billing), API keys,
          members and the audit log are kept. This cannot be undone. Owners only.
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <label htmlFor="cf" className="sr-only">Type the organization name to confirm</label>
          <input id="cf" value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder={`Type “${org.name}” to confirm`}
            disabled={role !== "owner"} className="h-10 w-72 rounded-xl border border-line px-3 text-[14px] disabled:bg-wash" />
          <Button variant="danger" disabled={role !== "owner" || confirm !== org.name || deleting} onClick={deleteData}>
            {deleting ? "Deleting…" : "Delete data"}
          </Button>
        </div>
        {delMsg && <p role={delMsg.error ? "alert" : "status"} className={`mt-3 max-w-2xl text-sm ${delMsg.error ? "text-reject" : "text-ink-2"}`}>{delMsg.text}</p>}
      </Card>
    </div>
  );
}
