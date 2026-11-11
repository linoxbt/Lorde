"use client";

import clsx from "clsx";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button, LinkButton } from "@/components/ui";

export type AuthMode = "signin" | "signup";
type Method = "password" | "link";

const input = "h-11 w-full rounded-xl border border-line px-3.5 text-[15px]";
export const MIN_PASSWORD = 8;
/** Supabase sends emails before it answers, which can take a while; tell the user, and never wait forever. */
const SLOW_MS = 5_000;
const GIVE_UP_MS = 45_000;

class SlowEmail extends Error {}

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return Promise.race([p, new Promise<T>((_, reject) => setTimeout(() => reject(new SlowEmail("timeout")), ms))]);
}

/** Create an account or sign in: email + password, a one-time email link, or GitHub. The user picks the method. */
export function SignInForm({ enabled, next, mode = "signin" }: { enabled: boolean; next: string; mode?: AuthMode }) {
  const router = useRouter();
  const [method, setMethod] = useState<Method>("password");
  const [email, setEmail] = useState(""), [password, setPassword] = useState(""), [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState<{ text: string; error?: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null), [slow, setSlow] = useState(false);
  useEffect(() => {
    setSlow(false);
    if (!busy) return;
    const t = setTimeout(() => setSlow(true), SLOW_MS);
    return () => clearTimeout(t);
  }, [busy]);
  if (!enabled) {
    return (
      <div className="space-y-4">
        <p className="rounded-xl border border-accent-soft bg-accent-wash p-4 text-sm text-accent-ink">
          Accounts need Supabase. Until it is configured, the Console runs on demo data.
        </p>
        <LinkButton href="/console" size="lg" className="w-full">Continue to the demo</LinkButton>
      </div>
    );
  }
  const signup = mode === "signup";
  const redirect = (to = next) => `${window.location.origin}/auth/callback?next=${encodeURIComponent(to)}`;
  const client = async () => (await import("@/lib/supabase/client")).supabaseBrowser();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    if (method === "password" && signup) {
      if (password.length < MIN_PASSWORD) return setMsg({ text: `Use at least ${MIN_PASSWORD} characters.`, error: true });
      if (password !== confirm) return setMsg({ text: "The passwords do not match.", error: true });
    }
    const sends = method === "link" || signup;
    setBusy(sends ? "Sending email…" : "Signing in…");
    try {
      const sb = await client();
      if (method === "link") {
        const { error } = await withTimeout(sb.auth.signInWithOtp({ email, options: { emailRedirectTo: redirect(), shouldCreateUser: signup } }), GIVE_UP_MS);
        if (error) throw error;
        setMsg({ text: signup ? "Check your email: the link creates your account and signs you in." : "Check your email for a sign-in link." });
      } else if (signup) {
        const { data, error } = await withTimeout(sb.auth.signUp({ email, password, options: { emailRedirectTo: redirect() } }), GIVE_UP_MS);
        if (error) throw error;
        if (data.session) return router.replace(next);
        // Supabase answers an existing, confirmed address with a user that has no identities.
        if (data.user && data.user.identities?.length === 0) {
          setMsg({ text: "An account with this email already exists. Sign in instead.", error: true });
        } else {
          setMsg({ text: "Check your email to confirm your address, then you are signed in." });
        }
      } else {
        const { error } = await withTimeout(sb.auth.signInWithPassword({ email, password }), GIVE_UP_MS);
        if (error) throw error;
        return router.replace(next);
      }
    } catch (err) {
      setMsg({ text: friendly(err), error: true });
    } finally {
      setBusy(null);
    }
  }

  async function forgot() {
    if (!email) return setMsg({ text: "Enter your email first, then choose “Forgot password”.", error: true });
    setBusy("Sending email…");
    setMsg(null);
    try {
      const { error } = await withTimeout((await client()).auth.resetPasswordForEmail(email, { redirectTo: redirect("/reset-password") }), GIVE_UP_MS);
      if (error) throw error;
      setMsg({ text: "If that email has an account, a reset link is on its way." });
    } catch (err) {
      setMsg({ text: friendly(err), error: true });
    } finally {
      setBusy(null);
    }
  }

  async function github() {
    const { error } = await (await client()).auth.signInWithOAuth({ provider: "github", options: { redirectTo: redirect() } });
    if (error) setMsg({ text: friendly(error), error: true });
  }

  const other = signup ? "/signin" : "/signup";
  const nextQs = next !== "/console" ? `?next=${encodeURIComponent(next)}` : "";
  return (
    <div className="space-y-5">
      <Button variant="secondary" size="lg" className="w-full" onClick={github}>
        {signup ? "Sign up with GitHub" : "Continue with GitHub"}
      </Button>
      <div className="flex items-center gap-3 text-[12px] text-faint"><span className="h-px flex-1 bg-line" />or with email<span className="h-px flex-1 bg-line" /></div>
      <div role="group" aria-label="How to use your address" className="grid grid-cols-2 rounded-full border border-line p-1 text-[13px]">
        {([["password", "Password"], ["link", "Email link"]] as const).map(([m, label]) => (
          <button key={m} type="button" aria-pressed={method === m} onClick={() => { setMethod(m); setMsg(null); }}
            className={clsx("h-8 rounded-full font-medium", method === m ? "bg-ink text-white" : "text-muted hover:text-ink")}>
            {label}
          </button>
        ))}
      </div>
      <form onSubmit={submit} className="space-y-3">
        <div className="space-y-1.5">
          <label htmlFor="em" className="text-sm font-medium">Email</label>
          <input id="em" type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)}
            className={input} placeholder="you@company.com" />
        </div>
        {method === "password" && (
          <div className="space-y-1.5">
            <div className="flex items-baseline justify-between">
              <label htmlFor="pw" className="text-sm font-medium">Password</label>
              {!signup && <button type="button" onClick={forgot} className="text-[13px] text-accent hover:underline">Forgot password?</button>}
            </div>
            <input id="pw" type="password" required minLength={signup ? MIN_PASSWORD : undefined}
              autoComplete={signup ? "new-password" : "current-password"} value={password}
              onChange={(e) => setPassword(e.target.value)} className={input} />
          </div>
        )}
        {method === "password" && signup && (
          <div className="space-y-1.5">
            <label htmlFor="pw2" className="text-sm font-medium">Confirm password</label>
            <input id="pw2" type="password" required autoComplete="new-password" value={confirm}
              onChange={(e) => setConfirm(e.target.value)} className={input} />
            <p className="text-[12px] text-faint">At least {MIN_PASSWORD} characters.</p>
          </div>
        )}
        <Button type="submit" size="lg" className="w-full" disabled={!!busy}>
          {busy ?? (method === "link" ? (signup ? "Email me a sign-up link" : "Email me a sign-in link")
            : signup ? "Create account" : "Sign in")}
        </Button>
      </form>
      {busy && slow && (
        <p role="status" className="text-sm text-muted">
          {busy === "Sending email…" ? "Still sending: the email service can take up to 30 seconds. Keep this page open."
            : "Still working: the sign-in service is slow right now."}
        </p>
      )}
      {msg && <p role={msg.error ? "alert" : "status"} className={clsx("text-sm", msg.error ? "text-reject" : "text-muted")}>{msg.text}</p>}
      <p className="text-sm text-muted">
        {signup ? "Already have an account? " : "New to Lorde? "}
        <Link href={`${other}${nextQs}`} className="font-medium text-accent hover:underline">{signup ? "Sign in" : "Create an account"}</Link>
      </p>
    </div>
  );
}

function friendly(err: unknown): string {
  if (err instanceof SlowEmail) {
    return "No answer from the sign-in service yet. If an email arrives in the next few minutes, its link works; otherwise try again.";
  }
  const m = err instanceof Error ? err.message : String(err);
  if (/invalid login credentials/i.test(m)) return "Wrong email or password.";
  if (/email not confirmed/i.test(m)) return "Confirm your email first: open the link we sent you.";
  if (/signups not allowed|otp.*disabled/i.test(m)) return "No account with this email yet. Create an account first.";
  if (/rate limit|too many/i.test(m)) return "Email limit reached for now (a few emails per hour). Use the link already sent, sign in with GitHub, or try again later.";
  return m;
}
