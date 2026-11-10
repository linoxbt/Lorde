"use client";

import { useState } from "react";

import { Button, LinkButton } from "@/components/ui";

export function SignInForm({ enabled, next }: { enabled: boolean; next: string }) {
  const [email, setEmail] = useState(""), [msg, setMsg] = useState(""), [busy, setBusy] = useState(false);
  if (!enabled) {
    return (
      <div className="space-y-4">
        <p className="rounded-xl border border-accent-soft bg-accent-wash p-4 text-sm text-accent-ink">
          Sign-in needs Supabase. Until it is configured, the Console runs on demo data.
        </p>
        <LinkButton href="/console" size="lg" className="w-full">Continue to the demo</LinkButton>
      </div>
    );
  }
  const redirect = () => `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`;
  async function magic(e: React.FormEvent) {
    e.preventDefault(); setBusy(true);
    const { supabaseBrowser } = await import("@/lib/supabase/client");
    const { error } = await supabaseBrowser().auth.signInWithOtp({ email, options: { emailRedirectTo: redirect() } });
    setBusy(false);
    setMsg(error ? error.message : "Check your email for a sign-in link.");
  }
  async function github() {
    const { supabaseBrowser } = await import("@/lib/supabase/client");
    await supabaseBrowser().auth.signInWithOAuth({ provider: "github", options: { redirectTo: redirect() } });
  }
  return (
    <div className="space-y-4">
      <Button variant="secondary" size="lg" className="w-full" onClick={github}>Continue with GitHub</Button>
      <div className="flex items-center gap-3 text-[12px] text-faint"><span className="h-px flex-1 bg-line" />or<span className="h-px flex-1 bg-line" /></div>
      <form onSubmit={magic} className="space-y-3">
        <label htmlFor="em" className="text-sm font-medium">Email</label>
        <input id="em" type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)}
          className="h-11 w-full rounded-xl border border-line px-3.5 text-[15px]" placeholder="you@company.com" />
        <Button type="submit" size="lg" className="w-full" disabled={busy}>{busy ? "Sending…" : "Email me a link"}</Button>
      </form>
      {msg && <p role="status" className="text-sm text-muted">{msg}</p>}
    </div>
  );
}
