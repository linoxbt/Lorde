import "server-only";

/** Server-side configuration. With nothing set, the Console runs on clearly labelled demo data. */
const gatewayUrl = (process.env.LORDE_GATEWAY_URL || "").replace(/\/$/, "");

export const env = {
  supabaseUrl: process.env.NEXT_PUBLIC_SUPABASE_URL || "",
  supabaseAnonKey: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "",
  gatewayUrl,
  /** Operator key for single-tenant self-hosting only: never used when Supabase accounts are configured. */
  apiKey: process.env.LORDE_API_KEY || "",
  keyPepper: process.env.LORDE_KEY_PEPPER || "",
  /** Public API base shown in docs and snippets; empty when neither the public nor the server URL is set. */
  publicApiUrl: (process.env.NEXT_PUBLIC_LORDE_API_URL || gatewayUrl).replace(/\/$/, ""),
  /** Optional: the model revision the Gateway serves, shown on /status when the Gateway does not report it. */
  modelRevision: (process.env.NEXT_PUBLIC_LORDE_MODEL_REVISION || "").trim(),
};
export const supabaseEnabled = () => Boolean(env.supabaseUrl && env.supabaseAnonKey);
