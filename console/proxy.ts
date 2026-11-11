import { createServerClient } from "@supabase/ssr";
import { type NextRequest, NextResponse } from "next/server";

/**
 * Refreshes the Supabase session and requires an account for /console: signed-out visitors go to /signin, and
 * signed-in users skip the sign-in and sign-up pages. Without Supabase configured, the Console runs in demo mode.
 */
export async function proxy(request: NextRequest) {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) return NextResponse.next();

  let response = NextResponse.next({ request });
  const supabase = createServerClient(url, key, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (all) => {
        all.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        all.forEach(({ name, value, options }) => response.cookies.set(name, value, options));
      },
    },
  });
  const { data } = await supabase.auth.getUser();
  const path = request.nextUrl.pathname;
  if (data.user && (path === "/signin" || path === "/signup")) {
    const to = request.nextUrl.clone();
    const next = to.searchParams.get("next");
    to.pathname = next?.startsWith("/console") && !next.startsWith("//") ? next : "/console";
    to.search = "";
    return NextResponse.redirect(to);
  }
  if (!data.user && path.startsWith("/console")) {
    const to = request.nextUrl.clone();
    to.pathname = "/signin";
    to.searchParams.set("next", request.nextUrl.pathname);
    return NextResponse.redirect(to);
  }
  return response;
}

export const config = { matcher: ["/console/:path*", "/auth/:path*", "/signin", "/signup", "/reset-password"] };
