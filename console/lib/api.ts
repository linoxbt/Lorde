import "server-only";

import { NextResponse } from "next/server";

/** The Console's JSON error envelope, the same shape as the Gateway's: {error: {code, message}}. */
export const apiError = (status: number, code: string, message: string, headers?: HeadersInit) =>
  NextResponse.json({ error: { code, message } }, { status, headers });

export const unauthenticated = () => apiError(401, "unauthenticated", "Sign in to the Console first.");

/** The verified session's user id (getClaims: local JWT check with asymmetric keys, else the Auth server). */
export async function sessionUserId(): Promise<string | null> {
  const { supabaseServer } = await import("./supabase/server");
  const { data } = await (await supabaseServer()).auth.getClaims();
  return typeof data?.claims?.sub === "string" ? data.claims.sub : null;
}
