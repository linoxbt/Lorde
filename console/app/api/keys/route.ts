import { NextResponse } from "next/server";
import { z } from "zod";

import { env, supabaseEnabled } from "@/lib/env";
import { hashKey, newKey, prefixOf } from "@/lib/keys";

const Create = z.object({
  name: z.string().min(1).max(60),
  scopes: z.array(z.enum(["judge", "jobs:read", "jobs:write", "usage:read"])).min(1),
  rpm: z.number().int().min(1).max(100_000),
});

/** Create a key: generated and hashed here, inserted under the user's session so RLS enforces owner/admin. */
export async function POST(req: Request) {
  const p = Create.safeParse(await req.json().catch(() => null));
  if (!p.success) return NextResponse.json({ error: { code: "invalid_request", message: p.error.issues[0]?.message } }, { status: 400 });
  const key = newKey();
  if (!supabaseEnabled()) {
    return NextResponse.json({ demo: true, key, row: { id: `demo-${Date.now()}`, name: p.data.name, prefix: prefixOf(key),
      scopes: p.data.scopes, rpm: p.data.rpm, created_at: new Date().toISOString(), last_used_at: null, revoked_at: null } });
  }
  if (env.keyPepper.length < 32) {
    return NextResponse.json({ error: { code: "not_configured", message: "LORDE_KEY_PEPPER is not set" } }, { status: 503 });
  }
  const { supabaseServer } = await import("@/lib/supabase/server");
  const sb = await supabaseServer();
  const { data: u } = await sb.auth.getUser();
  const { data: m } = await sb.from("org_members").select("org_id").eq("user_id", u.user?.id ?? "").limit(1).single();
  const { data, error } = await sb.from("api_keys").insert({
    org_id: m?.org_id, name: p.data.name, prefix: prefixOf(key), key_hash: hashKey(key, env.keyPepper),
    scopes: p.data.scopes, rpm: p.data.rpm, created_by: u.user?.id,
  }).select("id, name, prefix, scopes, rpm, created_at, last_used_at, revoked_at").single();
  if (error) return NextResponse.json({ error: { code: "forbidden", message: error.message } }, { status: 403 });
  return NextResponse.json({ key, row: data });
}

export async function DELETE(req: Request) {
  const id = new URL(req.url).searchParams.get("id") ?? "";
  if (!supabaseEnabled()) return NextResponse.json({ demo: true, id });
  const { supabaseServer } = await import("@/lib/supabase/server");
  const sb = await supabaseServer();
  const { data, error } = await sb.from("api_keys").update({ revoked_at: new Date().toISOString() }).eq("id", id).select("id");
  if (error || !data?.length) return NextResponse.json({ error: { code: "forbidden", message: error?.message ?? "not allowed" } }, { status: 403 });
  return NextResponse.json({ id });
}
