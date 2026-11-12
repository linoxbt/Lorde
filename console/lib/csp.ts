/**
 * The Content-Security-Policy proxy.ts sends with every page (pure: unit-tested in tests/unit/csp.test.ts).
 *
 * Scripts: only those carrying this response's nonce, and what they load ('strict-dynamic': Next's chunks, the
 * Turnstile script). Next applies the nonce to its own scripts when it renders the page, which is why every page is
 * rendered per request (app/layout.tsx). No 'unsafe-inline' and no 'unsafe-eval' for scripts in production;
 * `next dev` needs 'unsafe-eval' for React's debugging. Styles keep 'unsafe-inline': React `style` attributes
 * (progress bars, charts) cannot carry a nonce, and style injection cannot read cookies.
 *
 * The trade-off this does not remove: @supabase/ssr keeps the session in cookies that JavaScript can read (the
 * browser client must refresh the token), so an injected script would still reach the session. This CSP is what
 * keeps scripts from being injected; React's escaping is the other half.
 */
export type CspOptions = {
  nonce: string;
  dev?: boolean;
  /** NEXT_PUBLIC_SUPABASE_URL: Auth and PostgREST calls from the browser. */
  supabaseUrl?: string;
  /** NEXT_PUBLIC_LORDE_API_URL / the Gateway: signed upload and download URLs the browser fetches directly. */
  apiUrl?: string;
  /** Cloudflare Turnstile on the auth forms (NEXT_PUBLIC_TURNSTILE_SITE_KEY set). */
  turnstile?: boolean;
};

const TURNSTILE = "https://challenges.cloudflare.com";

function origin(u: string | undefined): string | null {
  if (!u) return null;
  try {
    const o = new URL(u).origin;
    return o === "null" ? null : o;
  } catch {
    return null;
  }
}

export function buildCsp({ nonce, dev = false, supabaseUrl, apiUrl, turnstile = false }: CspOptions): string {
  const sb = origin(supabaseUrl), api = origin(apiUrl);
  const connect = ["'self'", sb, sb?.replace(/^http/, "ws"), api, turnstile ? TURNSTILE : null];
  const directives: [string, (string | null | undefined | false)[]][] = [
    ["default-src", ["'self'"]],
    // 'self' and https: are ignored by browsers that understand 'strict-dynamic'; they only help very old ones.
    ["script-src", [`'nonce-${nonce}'`, "'strict-dynamic'", "'self'", turnstile && TURNSTILE, dev && "'unsafe-eval'"]],
    ["style-src", ["'self'", "'unsafe-inline'"]],
    ["img-src", ["'self'", "data:", "blob:"]],
    ["font-src", ["'self'", "data:"]],
    ["connect-src", dev ? [...connect, "ws:"] : connect],
    ["frame-src", [turnstile ? TURNSTILE : "'none'"]],
    ["worker-src", ["'self'", "blob:"]],
    ["object-src", ["'none'"]],
    ["base-uri", ["'self'"]],
    ["form-action", ["'self'"]],
    ["frame-ancestors", ["'none'"]],
  ];
  return directives.map(([k, v]) => `${k} ${[...new Set(v.filter(Boolean))].join(" ")}`).join("; ");
}

/** A fresh, unguessable nonce per response (128 random bits, base64). */
export function newNonce(): string {
  const b = crypto.getRandomValues(new Uint8Array(16));
  return btoa(String.fromCharCode(...b));
}
