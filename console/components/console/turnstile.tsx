"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { captchaFields } from "@/lib/captcha";

export { CaptchaMissing } from "@/lib/captcha";

/**
 * Optional Cloudflare Turnstile CAPTCHA for the auth forms. Enabled only when NEXT_PUBLIC_TURNSTILE_SITE_KEY is set
 * (and then Supabase Auth → Attack Protection → CAPTCHA must be on with the matching secret key, see
 * docs/DEPLOYMENT.md); otherwise nothing is rendered or loaded and `captchaOptions()` adds nothing.
 *
 * The script is added from JavaScript, so the nonce-based CSP's 'strict-dynamic' allows it (lib/csp.ts adds the
 * Turnstile frame and connect origins when the site key is set).
 * Tokens are single-use: call `reset()` after every auth request, successful or not.
 */
export const TURNSTILE_SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY || "";
const SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

type TurnstileApi = {
  render: (el: HTMLElement, opts: Record<string, unknown>) => string;
  reset: (id?: string) => void;
  remove: (id: string) => void;
};
declare global {
  interface Window { turnstile?: TurnstileApi }
}

let loading: Promise<void> | null = null;
function loadScript(): Promise<void> {
  if (window.turnstile) return Promise.resolve();
  loading ??= new Promise<void>((resolve, reject) => {
    const s = document.createElement("script");
    s.src = SRC;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => { loading = null; s.remove(); reject(new Error("Turnstile did not load")); };
    document.head.appendChild(s);
  });
  return loading;
}

export function useCaptcha(siteKey = TURNSTILE_SITE_KEY) {
  const enabled = Boolean(siteKey);
  const [token, setToken] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const el = useRef<HTMLDivElement>(null);
  const id = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    loadScript().then(() => {
      if (cancelled || !el.current || !window.turnstile) return;
      id.current = window.turnstile.render(el.current, {
        sitekey: siteKey,
        size: "flexible",
        theme: "light",
        callback: (t: string) => { setToken(t); setFailed(false); },
        "expired-callback": () => setToken(null),
        "error-callback": () => { setToken(null); setFailed(true); },
      });
    }).catch(() => { if (!cancelled) setFailed(true); });
    return () => {
      cancelled = true;
      if (id.current) window.turnstile?.remove(id.current);
      id.current = null;
    };
  }, [enabled, siteKey]);

  const reset = useCallback(() => {
    setToken(null);
    if (id.current) window.turnstile?.reset(id.current);
  }, []);

  /** `{ captchaToken }` for supabase-js auth calls; `{}` when CAPTCHA is off. Throws CaptchaMissing if not solved. */
  const captchaOptions = useCallback(() => captchaFields(siteKey, token), [siteKey, token]);

  const widget = enabled ? (
    <div role="group" aria-label="Security check">
      <div ref={el} className="min-h-[65px]" />
      {failed && <p role="alert" className="mt-1 text-[13px] text-reject">The security check did not load. Reload the page and try again.</p>}
    </div>
  ) : null;

  return { enabled, token, reset, captchaOptions, widget };
}
