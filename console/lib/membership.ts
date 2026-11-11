import "server-only";

import type { supabaseServer } from "./supabase/server";
import type { Role } from "./types";

export type Client = Awaited<ReturnType<typeof supabaseServer>>;

/**
 * The organization the Console works in for this user: their oldest membership (ties broken by org id), so every
 * screen and API route picks the same one. Null when the user belongs to no organization.
 */
export async function primaryMembership(sb: Client, userId: string): Promise<{ org_id: string; role: Role } | null> {
  const { data, error } = await sb.from("org_members").select("org_id, role, created_at").eq("user_id", userId)
    .order("created_at", { ascending: true }).order("org_id", { ascending: true }).limit(1).maybeSingle();
  if (error) throw new DataError("your organization membership", error);
  return data ? { org_id: data.org_id as string, role: data.role as Role } : null;
}

/** A Supabase read failed: logged with details on the server; the page shows the error boundary. */
export class DataError extends Error {
  constructor(what: string, cause: { message?: string; code?: string } | null) {
    super(`Could not load ${what}`);
    console.error(`[console] loading ${what} failed:`, cause?.code ?? "", cause?.message ?? cause);
  }
}

export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
