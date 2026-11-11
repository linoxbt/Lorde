import { finishAuth } from "@/lib/auth-link";

/**
 * Email links that work on any device: /auth/confirm?token_hash={{ .TokenHash }}&type=<type>&next=<path or
 * {{ .RedirectTo }}>. Verified server-side with verifyOtp, so no PKCE verifier from the requesting browser is needed.
 */
export const GET = (request: Request) => finishAuth(request);
