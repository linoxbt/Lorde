import { createServerClient } from "@supabase/ssr";
import { type NextRequest, NextResponse } from "next/server";

import { safeNext } from "@/lib/safe-next";

/**
 * Refreshes the Supabase session and requires an account for /console: signed-out visitors go to /signin (with
 * `next` keeping the path and query), and signed-in users skip the sign-in and sign-up pages. The decision uses
 * getClaims() (a local JWT check with asymmetric keys); pages that need the verified user still call getUser().
 * Without Supabase configured, the Console runs in demo mode.
 */
export async function proxy(request: NextRequest) {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) return NextResponse.next();

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
  const path = request.nextUrl.pathname;

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
  matcher: ["/console/:path*", "/auth/:path*", "/signin", "/signup", "/reset-password", "/welcome", "/api/:path*"],
};
