"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { MIN_PASSWORD } from "@/components/console/signin-form";
import { Button } from "@/components/ui";
import { friendlyAuthError } from "@/lib/auth-errors";

const input = "h-11 w-full rounded-xl border border-line px-3.5 text-[15px]";

/** Set a new password. The reset email's link signs the user in through /auth/callback first. */
export function ResetForm() {
  const router = useRouter();
  const [password, setPassword] = useState(""), [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState(""), [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (password.length < MIN_PASSWORD) return setMsg(`Use at least ${MIN_PASSWORD} characters.`);
    if (password !== confirm) return setMsg("The passwords do not match.");
    if (busy) return;
    setBusy(true); setMsg("");
    try {
      const { supabaseBrowser } = await import("@/lib/supabase/client");
      const { error } = await supabaseBrowser().auth.updateUser({ password });
      if (error) return setMsg(/session/i.test(error.message) ? "This reset link has expired. Request a new one from the sign-in page." : friendlyAuthError(error).text);
      router.replace("/console");
    } catch {
      // A network error (or a failed chunk load) must not leave the button stuck on "Saving…".
      setMsg("Could not reach the sign-in service. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="space-y-3">
      <label htmlFor="pw" className="text-sm font-medium">New password</label>
      <input id="pw" type="password" required minLength={MIN_PASSWORD} autoComplete="new-password" value={password}
        onChange={(e) => setPassword(e.target.value)} className={input} />
      <label htmlFor="pw2" className="text-sm font-medium">Confirm new password</label>
      <input id="pw2" type="password" required autoComplete="new-password" value={confirm}
        onChange={(e) => setConfirm(e.target.value)} className={input} />
      <Button type="submit" size="lg" className="w-full" disabled={busy}>{busy ? "Saving…" : "Save password"}</Button>
      {msg && <p role="alert" className="text-sm text-reject">{msg}</p>}
    </form>
  );
}
