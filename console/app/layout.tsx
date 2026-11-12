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

export const viewport: Viewport = { themeColor: "#ffffff", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
