import type { Metadata } from "next";
import Link from "next/link";

import { Logo } from "@/components/brand";
import { SignInForm } from "@/components/console/signin-form";
import { supabaseEnabled } from "@/lib/env";

export const metadata: Metadata = { title: "Sign in" };

export default async function SignIn(props: PageProps<"/signin">) {
  const sp = await props.searchParams;
  const next = typeof sp.next === "string" && sp.next.startsWith("/console") ? sp.next : "/console";
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="flex flex-col px-6 py-8 sm:px-12">
        <Logo />
        <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-16">
          <h1 className="text-[34px] font-medium tracking-[-0.04em]">Sign in to Lorde</h1>
          <p className="mt-2 text-[15px] text-muted">An organization is created for you on first sign-in.</p>
          <div className="mt-8"><SignInForm enabled={supabaseEnabled()} next={next} /></div>
          <p className="mt-8 text-[13px] text-muted">By continuing you agree to the Cohere Labs Acceptable Use Policy for model outputs. <Link href="/model#licence" className="text-accent hover:underline">Licences</Link></p>
        </div>
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
