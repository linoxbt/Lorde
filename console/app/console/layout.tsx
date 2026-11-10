import type { Metadata } from "next";

import { ConsoleShell } from "@/components/console/shell";
import { getWorkspace } from "@/lib/data";

export const metadata: Metadata = { title: { default: "Console", template: "%s · Lorde Console" }, robots: { index: false } };
export const dynamic = "force-dynamic";

export default async function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const ws = await getWorkspace();
  return <ConsoleShell ws={ws}>{children}</ConsoleShell>;
}
