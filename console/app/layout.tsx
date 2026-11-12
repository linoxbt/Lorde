import type { Metadata, Viewport } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import { siteUrl } from "@/lib/site";

import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: { default: "Lorde · The judge for trustworthy data", template: "%s · Lorde" },
  description:
    "Lorde is a 3B judge model that runs CuratorKIT's quality gates: API-grade decisions on one inexpensive GPU.",
  openGraph: { title: "Lorde", description: "The judge for trustworthy training data.", type: "website" },
};

/**
 * Every page renders per request: the nonce-based Content-Security-Policy (proxy.ts, lib/csp.ts) is new for each
 * response, and Next can only put it on its scripts while rendering. A prerendered page would carry no nonce and its
 * scripts would be blocked.
 */
export const dynamic = "force-dynamic";

export const viewport: Viewport = { themeColor: "#ffffff", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body>
        {/* First stop for keyboard users: past the header and navigation to the page's own content. */}
        <a href="#main" className="sr-only z-[60] rounded-full bg-ink px-4 py-2 text-sm font-medium text-white focus:not-sr-only focus:fixed focus:left-4 focus:top-4">
          Skip to main content
        </a>
        {children}
      </body>
    </html>
  );
}
