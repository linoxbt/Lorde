import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The live-mode e2e tests build a second copy (pointed at a fake Supabase) into .next-live.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          // Minimal CSP that leaves Next's inline bootstrap scripts alone (a script-src would need per-request nonces,
          // which would make every page dynamic): no plugins, no <base> hijack, no framing, forms post only here.
          { key: "Content-Security-Policy", value: "base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'" },
        ],
      },
    ];
  },
};

export default nextConfig;
