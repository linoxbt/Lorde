import { NextResponse } from "next/server";

import { supabaseEnabled } from "@/lib/env";

/** Ends the Supabase session on this device only (POST only, so a link or prefetch cannot sign anyone out). */
export async function POST(request: Request) {
  if (supabaseEnabled()) {
    const { supabaseServer } = await import("@/lib/supabase/server");
    await (await supabaseServer()).auth.signOut({ scope: "local" });
  }
  return NextResponse.redirect(new URL("/signin", request.url), { status: 303 });
}
