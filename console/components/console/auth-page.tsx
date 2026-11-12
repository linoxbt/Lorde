import Link from "next/link";
import type { ReactNode } from "react";

import { Logo } from "@/components/brand";

/** Two-column layout shared by sign-in, sign-up and password reset. */
export function AuthPage({ title, lead, error, notice, children }:
  { title: string; lead: string; error?: string; notice?: string; children: ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="flex flex-col px-6 py-8 sm:px-12">
        <Logo />
        <main id="main" tabIndex={-1} className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-16 outline-none">
          <h1 className="text-[34px] font-medium tracking-[-0.04em]">{title}</h1>
          <p className="mt-2 text-[15px] text-muted">{lead}</p>
          {error && <p role="alert" className="mt-6 rounded-xl border border-reject/30 bg-reject/5 p-3 text-sm text-reject">{error}</p>}
          {notice && <p role="status" className="mt-6 rounded-xl border border-pass/30 bg-pass-wash p-3 text-sm text-ink">{notice}</p>}
          <div className="mt-8">{children}</div>
          <p className="mt-8 text-[13px] text-muted">By continuing you agree to the Cohere Labs Acceptable Use Policy for model outputs. <Link href="/model#licence" className="text-accent hover:underline">Licences</Link></p>
        </main>
      </div>
      <div className="relative hidden overflow-hidden bg-accent lg:block" aria-hidden>
        {[0, 1, 2, 3, 4].map((i) => (
          <span key={i} className="absolute left-1/2 top-1/2 rounded-full border border-white/20"
            style={{ width: 220 + i * 170, height: 220 + i * 170, transform: "translate(-50%, -50%)" }} />
        ))}
        <span className="absolute left-1/2 top-1/2 size-24 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white" />
        <p className="absolute bottom-10 left-10 right-10 text-[22px] font-medium leading-snug tracking-[-0.02em] text-white">
          “A judge is only as good as the data it lets through.”
        </p>
      </div>
    </div>
  );
}
