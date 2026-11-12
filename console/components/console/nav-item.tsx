"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

export function NavItem({ href, label, icon }: { href: string; label: string; icon?: ReactNode }) {
  const path = usePathname();
  const active = href === "/console" ? path === href
    : href === "/console/jobs" ? path === href || (/^\/console\/jobs\/(?!new)/.test(path))
    : path.startsWith(href);
  return (
    <Link href={href} aria-current={active ? "page" : undefined}
      className={clsx("flex items-center gap-2.5 rounded-lg px-3 py-2 text-[14px] transition-colors",
        active ? "bg-white font-medium text-ink shadow-[0_0_0_1px_var(--color-line)]" : "text-ink-2 hover:bg-white/70 hover:text-ink")}>
      <span className={active ? "text-accent" : "text-faint"}>{icon}</span>{label}
    </Link>
  );
}
