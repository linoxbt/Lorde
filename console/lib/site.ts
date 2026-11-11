/** The site's own origin, for metadata and absolute links (set NEXT_PUBLIC_SITE_URL per deployment). */
export const siteUrl = (process.env.NEXT_PUBLIC_SITE_URL || "https://lorde-console.vercel.app").replace(/\/$/, "");
