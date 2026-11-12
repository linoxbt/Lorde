import { createServerClient } from "@supabase/ssr";
import { type NextRequest, NextResponse } from "next/server";

import { buildCsp, newNonce } from "@/lib/csp";
import { safeNext } from "@/lib/safe-next";

const PATH_HEADER = "x-lorde-path";  // read by lib/data.ts
/** Paths that need the Supabase session (refresh, and the sign-in gate); the CSP applies everywhere. */
const SESSION = /^\/(console|auth|api|signin|signup|reset-password|welcome)(\/|$)/;

/**
 * Every page: a nonce-based Content-Security-Policy (lib/csp.ts). Next reads the nonce from the request's CSP header
 * and puts it on its own scripts while rendering, so pages render per request (app/layout.tsx).
 *
 * Session paths: refreshes the Supabase session and requires an account for /console: signed-out visitors go to
 * /signin (with `next` keeping the path and query), and signed-in users skip the sign-in and sign-up pages. The
 * decision uses getClaims() (a local JWT check with asymmetric keys); pages that need the verified user still call
 * getUser(). Without Supabase configured, the Console runs in demo mode.
 */
export async function proxy(request: NextRequest) {
  const nonce = newNonce();
  const csp = buildCsp({
    nonce,
    dev: process.env.NODE_ENV === "development",
    supabaseUrl: process.env.NEXT_PUBLIC_SUPABASE_URL,
    apiUrl: process.env.NEXT_PUBLIC_LORDE_API_URL || process.env.LORDE_GATEWAY_URL,
    turnstile: Boolean(process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY),
  });
  // Request headers for the render (set here only: client values are overwritten).
  request.headers.set("content-security-policy", csp);
  request.headers.set("x-nonce", nonce);
  request.headers.set(PATH_HEADER, `${request.nextUrl.pathname}${request.nextUrl.search}`);
  const response = await session(request);
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

async function session(request: NextRequest): Promise<NextResponse> {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  const path = request.nextUrl.pathname;
  if (!url || !key || !SESSION.test(path)) return NextResponse.next({ request });

  let response = NextResponse.next({ request });
  const extraHeaders: Record<string, string> = {};
  const supabase = createServerClient(url, key, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (all, headers) => {
        all.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        all.forEach(({ name, value, options }) => response.cookies.set(name, value, options));
        Object.assign(extraHeaders, headers);
        Object.entries(extraHeaders).forEach(([k, v]) => response.headers.set(k, v));
      },
    },
  });
  const { data } = await supabase.auth.getClaims();
  const signedIn = Boolean(data?.claims?.sub);

  // A redirect must carry any cookies the refresh just rotated, or the new refresh token is lost.
  const redirect = (to: URL) => {
    const r = NextResponse.redirect(to);
    response.cookies.getAll().forEach((c) => r.cookies.set(c));
    Object.entries(extraHeaders).forEach(([k, v]) => r.headers.set(k, v));
    return r;
  };

  if (signedIn && (path === "/signin" || path === "/signup")) {
    const next = safeNext(request.nextUrl.searchParams.get("next"));
    return redirect(new URL(next, request.nextUrl.origin));
  }
  if (!signedIn && path.startsWith("/console")) {
    const to = new URL("/signin", request.nextUrl.origin);
    to.searchParams.set("next", `${path}${request.nextUrl.search}`);
    return redirect(to);
  }
  return response;
}

export const config = {
  // Everything but build assets and static files.
  matcher: ["/((?!_next/static|_next/image|favicon\\.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|txt|xml|woff2?)$).*)"],
};
