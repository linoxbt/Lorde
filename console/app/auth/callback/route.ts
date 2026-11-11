import { finishAuth } from "@/lib/auth-link";

/**
 * Supabase redirects here after GitHub sign-in and for email links that carry a PKCE `code` (emailRedirectTo).
 * Links with `token_hash` + `type` are accepted here too; email templates should point at /auth/confirm.
 */
export const GET = (request: Request) => finishAuth(request);
