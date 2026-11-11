import { NextResponse } from "next/server";
import { z } from "zod";

import { apiError, unauthenticated } from "@/lib/api";
import { env, supabaseEnabled } from "@/lib/env";
import { hashKey, newKey, prefixOf } from "@/lib/keys";
import { primaryMembership, UUID } from "@/lib/membership";
import { MAX_RPM, SCOPES } from "@/lib/scopes";

export const dynamic = "force-dynamic";

const Create = z.object({
  name: z.string().trim().min(1, "Give the key a name").max(60),
  scopes: z.array(z.enum(SCOPES)).min(1, "Pick at least one scope").transform((s) => [...new Set(s)]),
  rpm: z.number().int().min(1).max(MAX_RPM, `At most ${MAX_RPM} judgments per minute`),
});
const COLS = "id, name, prefix, scopes, rpm, created_at, last_used_at, revoked_at";

async function session() {
  const { supabaseServer } = await import("@/lib/supabase/server");
  const sb = await supabaseServer();
  const { data } = await sb.auth.getUser();
  return { sb, user: data.user };
}

/** Create a key: generated and hashed here, inserted under the user's session so RLS enforces owner/admin. */
export async function POST(req: Request) {
  const s = supabaseEnabled() ? await session() : null;
  if (s && !s.user) return unauthenticated();
  const p = Create.safeParse(await req.json().catch(() => null));
  if (!p.success) return apiError(400, "invalid_request", p.error.issues[0]?.message ?? "Invalid request");
  const key = newKey();
  if (!s) {
    return NextResponse.json({ demo: true, key, row: { id: `demo-${Date.now()}`, name: p.data.name, prefix: prefixOf(key),
      scopes: p.data.scopes, rpm: p.data.rpm, created_at: new Date().toISOString(), last_used_at: null, revoked_at: null } });
  }
  const { sb, user } = s;
  if (!user) return unauthenticated();
  if (env.keyPepper.length < 32) return apiError(503, "not_configured", "Key creation is not configured on this Console.");
  try {
    const m = await primaryMembership(sb, user.id);
    if (!m) return apiError(403, "forbidden", "Your account is not in an organization.");
    if (m.role !== "owner" && m.role !== "admin") return apiError(403, "forbidden", "Only owners and admins can create keys.");
    const { data, error } = await sb.from("api_keys").insert({
      org_id: m.org_id, name: p.data.name, prefix: prefixOf(key), key_hash: hashKey(key, env.keyPepper),
      scopes: p.data.scopes, rpm: p.data.rpm, created_by: user.id,
    }).select(COLS).single();
    if (error) {
      console.error("[console] key insert failed:", error.code, error.message);
      return error.code === "42501"
        ? apiError(403, "forbidden", "Only owners and admins can create keys.")
        : apiError(500, "internal", "Could not create the key. Try again.");
    }
    return NextResponse.json({ key, row: data });
  } catch (e) {
    console.error("[console] key creation failed:", e);
    return apiError(500, "internal", "Could not create the key. Try again.");
  }
}

/** Revoke a key (RLS: owners and admins of its organization). */
export async function DELETE(req: Request) {
  const id = new URL(req.url).searchParams.get("id") ?? "";
  if (!supabaseEnabled()) return NextResponse.json({ demo: true, id });
  if (!UUID.test(id)) return apiError(400, "invalid_request", "Not a key id.");
  const { sb, user } = await session();
  if (!user) return unauthenticated();
  const { data, error } = await sb.from("api_keys").update({ revoked_at: new Date().toISOString() })
    .eq("id", id).is("revoked_at", null).select("id, revoked_at");
  if (error) {
    console.error("[console] key revoke failed:", error.code, error.message);
    return apiError(500, "internal", "Could not revoke the key. Try again.");
  }
  if (!data?.length) return apiError(404, "not_found", "No active key with that id that you can revoke.");
  return NextResponse.json({ id, revoked_at: data[0]!.revoked_at });
}
