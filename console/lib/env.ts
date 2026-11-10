/** Server-side configuration. With nothing set, the Console runs on clearly labelled demo data. */
export const env = {
  supabaseUrl: process.env.NEXT_PUBLIC_SUPABASE_URL || "",
  supabaseAnonKey: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "",
  gatewayUrl: (process.env.LORDE_GATEWAY_URL || "").replace(/\/$/, ""),
  apiKey: process.env.LORDE_API_KEY || "",
  keyPepper: process.env.LORDE_KEY_PEPPER || "",
  publicApiUrl: process.env.NEXT_PUBLIC_LORDE_API_URL || "https://api.<domain>",
};
export const supabaseEnabled = () => Boolean(env.supabaseUrl && env.supabaseAnonKey);
export const gatewayEnabled = () => Boolean(env.gatewayUrl && env.apiKey);
export const isDemo = () => !supabaseEnabled();
