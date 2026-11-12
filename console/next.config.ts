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
          // The Content-Security-Policy is per response (a fresh script nonce): proxy.ts and lib/csp.ts.
        ],
      },
    ];
  },
};

export default nextConfig;
