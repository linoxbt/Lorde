/**
 * Post-sign-in destinations: only same-origin paths under the allowed prefixes, query string kept.
 * Anything else (absolute or protocol-relative URLs, backslashes, `..` escaping the prefix) becomes the fallback.
 */
const BASE = "http://lorde.invalid";

export function safeNext(raw: unknown, allowed: readonly string[] = ["/console"], fallback = "/console"): string {
  if (typeof raw !== "string" || !raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return fallback;
  // Control characters are stripped by URL parsing and can turn "/\t/evil" into "//evil": refuse them outright.
  if (/[\u0000-\u001f\u007f]/.test(raw)) return fallback;
  let u: URL;
  try {
    u = new URL(raw, BASE);
  } catch {
    return fallback;
  }
  if (u.origin !== BASE) return fallback;
  if (!allowed.some((p) => u.pathname === p || u.pathname.startsWith(`${p}/`))) return fallback;
  return `${u.pathname}${u.search}`;
}

const DESTINATIONS = ["/console", "/reset-password"] as const;

/**
 * Where to go after the link. `next` may be a path, or the full emailRedirectTo URL a template passed as
 * {{ .RedirectTo }} (https://<site>/auth/callback?next=/console/...), whose own `next` is then used.
 */
export function destination(rawNext: string | null, origin: string, type?: string | null): string {
  if (type === "recovery") return "/reset-password";
  let next = rawNext;
  if (next && /^https?:\/\//i.test(next)) {
    try {
      const u = new URL(next);
      next = u.origin === origin ? (u.pathname.startsWith("/auth/") ? u.searchParams.get("next") : `${u.pathname}${u.search}`) : null;
    } catch {
      next = null;
    }
  }
  return safeNext(next, DESTINATIONS, "/console");
}
