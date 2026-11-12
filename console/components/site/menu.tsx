"use client";

import clsx from "clsx";
import { ChevronRight, Menu as MenuIcon, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { Logo } from "@/components/brand";
import { buttonClass } from "@/components/ui";
import type { NavGroup, NavLink } from "@/lib/nav";

/**
 * Full-screen hamburger menu with expandable groups. Escape closes; focus moves in and back out. Any link click
 * closes it too: a same-page link (/docs#api from /docs) changes no pathname, so the pathname effect alone would leave
 * the menu covering the section the user asked for.
 */
export function HamburgerMenu({ groups, links, cta = { label: "Open console", href: "/console" }, title }:
  { groups?: NavGroup[]; links?: NavLink[]; cta?: NavLink; title?: string }) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const pathname = usePathname();

  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel.current?.querySelector<HTMLElement>("button, a")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
      if (e.key === "Tab" && panel.current) {
        const f = [...panel.current.querySelectorAll<HTMLElement>("a, button")];
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
      button.current?.focus();
    };
  }, [open]);

  return (
    <>
      <button
        ref={button}
        type="button"
        aria-label="Open menu"
        aria-expanded={open}
        aria-controls="site-menu"
        onClick={() => setOpen(true)}
        className="inline-flex size-10 items-center justify-center rounded-full border border-line bg-white text-ink transition-colors hover:bg-wash"
      >
        <MenuIcon className="size-[18px]" aria-hidden />
      </button>
      {open && createPortal(
        <div
          id="site-menu"
          ref={panel}
          role="dialog"
          aria-modal="true"
          aria-label={title ?? "Menu"}
          className="fixed inset-0 z-50 flex flex-col overflow-y-auto bg-white"
          onClick={(e) => { if ((e.target as HTMLElement).closest("a[href]")) { setOpen(false); setExpanded(null); } }}
        >
          <div className="mx-auto flex h-[72px] w-full max-w-[1200px] shrink-0 items-center justify-between px-5 sm:px-8">
            <Logo />
            <button
              type="button"
              aria-label="Close menu"
              onClick={() => setOpen(false)}
              className="inline-flex size-10 items-center justify-center rounded-full hover:bg-wash"
            >
              <X className="size-5" aria-hidden />
            </button>
          </div>
          <nav className="mx-auto w-full max-w-[1200px] flex-1 px-5 pb-10 sm:px-8">
            {title && <p className="mb-2 mt-4 font-mono text-[11px] uppercase tracking-[0.14em] text-muted">{title}</p>}
            <ul className={clsx("divide-y divide-line border-b border-line md:grid md:gap-x-10 md:divide-y-0 md:border-0",
              groups ? "md:grid-cols-4" : "md:grid-cols-2")}>
              {groups?.map((g) => {
                const isOpen = expanded === g.label;
                return (
                  <li key={g.label} className="md:border-0">
                    <button
                      type="button"
                      aria-expanded={isOpen}
                      onClick={() => setExpanded(isOpen ? null : g.label)}
                      className="flex w-full items-center justify-between py-5 text-left text-[22px] font-medium tracking-[-0.02em] sm:text-[26px] md:pointer-events-none md:pb-3"
                    >
                      {g.label}
                      <ChevronRight className={clsx("size-5 transition-transform md:hidden", isOpen && "rotate-90")} aria-hidden />
                    </button>
                    {(
                      <ul className={clsx("gap-1 pb-5 md:-mx-3 md:grid", isOpen ? "grid" : "hidden")}>
                        {g.links.map((l) => (
                          <li key={l.href + l.label}>
                            <Link href={l.href} className="group block rounded-xl px-3 py-2.5 hover:bg-wash">
                              <span className="text-[15px] font-medium">{l.label}</span>
                              {l.desc && <span className="block text-[13px] text-muted">{l.desc}</span>}
                            </Link>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
              {links?.map((l) => (
                <li key={l.href} className="md:border-b md:border-line">
                  <Link
                    href={l.href}
                    aria-current={pathname === l.href ? "page" : undefined}
                    className={clsx(
                      "flex items-center justify-between py-5 text-[22px] font-medium tracking-[-0.02em] sm:text-[26px]",
                      pathname === l.href && "text-accent",
                    )}
                  >
                    {l.label}
                    <ChevronRight className="size-5" aria-hidden />
                  </Link>
                </li>
              ))}
            </ul>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href={cta.href} className={buttonClass("primary", "lg")}>{cta.label}</Link>
              <Link href="/docs" className={buttonClass("secondary", "lg")}>Read the docs</Link>
            </div>
          </nav>
        </div>,
        // Portal to <body>: the sticky header's backdrop-filter would otherwise contain the fixed overlay.
        document.body,
      )}
    </>
  );
}
