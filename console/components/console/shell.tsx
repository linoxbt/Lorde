import { BarChart3, LogOut, Database, FileText, FlaskConical, Gauge, KeyRound, ListChecks, PlusCircle, Settings, UserCheck } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Logo } from "@/components/brand";
import { NavItem } from "@/components/console/nav-item";
import { HamburgerMenu } from "@/components/site/menu";
import { consoleNav } from "@/lib/nav";
import type { Workspace } from "@/lib/types";

const ICONS: Record<string, ReactNode> = {
  "/console": <Gauge className="size-4" />, "/console/datasets": <Database className="size-4" />,
  "/console/jobs": <ListChecks className="size-4" />, "/console/jobs/new": <PlusCircle className="size-4" />,
  "/console/review": <UserCheck className="size-4" />, "/console/playground": <FlaskConical className="size-4" />,
  "/console/reports": <FileText className="size-4" />, "/console/keys": <KeyRound className="size-4" />,
  "/console/settings": <Settings className="size-4" />,
};

export function ConsoleShell({ ws, children }: { ws: Workspace; children: ReactNode }) {
  return (
    <div className="min-h-screen bg-white lg:grid lg:grid-cols-[248px_1fr]">
      <aside className="hidden border-r border-line bg-wash/60 lg:flex lg:flex-col">
        <div className="flex h-16 items-center px-5"><Logo href="/" /></div>
        <div className="mx-3 mb-3 rounded-xl border border-line bg-white px-3 py-2.5">
          <p className="truncate text-sm font-medium">{ws.org.name}</p>
          <p className="truncate text-[12px] text-muted">{ws.me.email} · {ws.me.role}</p>
        </div>
        <nav aria-label="Console" className="flex-1 space-y-0.5 px-3">
          {consoleNav.map((l) => <NavItem key={l.href} href={l.href} label={l.label} icon={ICONS[l.href]} />)}
        </nav>
        <div className="space-y-2 p-4 text-[12px] text-muted">
          <p className="flex items-center gap-2"><BarChart3 className="size-3.5" /> Model <span className="font-mono text-ink-2">{ws.activeModel.repo}@{ws.activeModel.revision}</span></p>
          <Link href="/docs" className="block hover:text-ink">Docs</Link>
          {ws.mode === "live" && <SignOut className="flex items-center gap-2 hover:text-ink" />}
        </div>
      </aside>
      <div className="min-w-0">
        {ws.mode === "demo" && (
          <div className="border-b border-accent-soft bg-accent-wash px-5 py-2 text-center text-[13px] text-accent-ink">
            Demo workspace: sample data, nothing is saved. Connect Supabase and the Gateway to go live.{" "}
            <Link href="/docs" className="font-medium underline underline-offset-2">How</Link>
          </div>
        )}
        <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-line bg-white/90 px-5 backdrop-blur lg:px-8">
          <div className="lg:hidden"><Logo href="/console" /></div>
          <p className="hidden text-sm text-muted lg:block">Lorde Console</p>
          <div className="flex items-center gap-2">
            {ws.mode === "live" && <SignOut className="flex h-9 items-center gap-2 rounded-full px-3 text-[13px] text-muted hover:bg-wash hover:text-ink lg:hidden" />}
            <Link href="/console/jobs/new" className="hidden h-9 items-center rounded-full bg-accent px-4 text-[13px] font-medium text-white hover:bg-accent-hover sm:inline-flex">New job</Link>
            <div className="lg:hidden"><HamburgerMenu links={consoleNav} title="Console" cta={{ label: "New job", href: "/console/jobs/new" }} /></div>
          </div>
        </header>
        <main className="px-5 py-8 lg:px-8 lg:py-10">{children}</main>
      </div>
    </div>
  );
}

function SignOut({ className }: { className: string }) {
  return (
    <form action="/auth/signout" method="post">
      <button type="submit" className={className}><LogOut className="size-3.5" /> Sign out</button>
    </form>
  );
}
