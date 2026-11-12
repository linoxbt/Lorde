# Lorde Console

The web app for Lorde: the marketing site (`/`, `/docs`, `/bench`, `/model`, `/status`) and the Console
(`/console/*`): overview, datasets, jobs, new job, job detail, review, playground, reports, API keys and settings.
Every page is its own route. Next.js 16 (App Router), React 19, TypeScript (strict), Tailwind CSS 4, Geist,
Recharts, Supabase (`@supabase/ssr`), zod, Playwright.

```bash
pnpm install
pnpm dev                 # http://localhost:3000, demo data until env vars are set
pnpm typecheck && pnpm test:unit && pnpm build
pnpm test:e2e            # Playwright, demo mode: desktop and phone projects
pnpm test:e2e:live       # Playwright, live mode: production build (.next-live) against tests/fixtures/fake-supabase.mjs
```

## Modes

- **Demo** (no env): every page runs on deterministic, clearly labelled sample data; the playground uses a transparent
  heuristic labelled "Simulated". Nothing is saved.
- **Live**: set `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` (see `.env.example`). An account is required: `/signup` creates one (email + password with
  email confirmation, a one-time email link, or GitHub; the user picks), `/signin` signs in, "Forgot password" leads to
  `/reset-password`, and `proxy.ts` sends signed-out visitors from `/console` to `/signin`; pages read through the user's own session, so the RLS
  policies in `../supabase/migrations` decide what is visible. Keys are created server-side with `LORDE_KEY_PEPPER`
  (same hash as the Gateway) and inserted under RLS, so only owners and admins can create or revoke them; a database
  trigger audits every change.
- **Gateway calls** (playground, uploads, jobs) go through `/api/gateway/*`, an allow-listed proxy to
  `LORDE_GATEWAY_URL` that needs a signed-in session and forwards the user's own API key from this browser tab (never
  stored server-side). `LORDE_API_KEY` is used only without Supabase (single-tenant self-hosting).
- **Database functions**: the Console has no service-role key. Aggregates (daily usage, pass rates, histograms, the
  review queue, member emails) and owner-only data deletion are SQL functions in
  `../supabase/migrations/0006_console.sql`, called with the user's session; apply it before deploying.
- **Email links** work on any device once the five templates in `../supabase/templates/` are applied (steps in
  `../docs/DEPLOYMENT.md`, "Supabase Auth: email templates"): they link to
  `{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=<type>&next={{ .RedirectTo }}`, verified server-side
  (`verifyOtp`). `/auth/callback` keeps handling GitHub and PKCE `code` links; a PKCE email link opened in another
  browser gets "your email is confirmed, sign in" (sign-up) or "request a new link" (sign-in link, reset).

## Design

Layout and rhythm take cues from base.org (oversized tight headlines, pill buttons, hairline cards with line
diagrams, a full-bleed CTA band, mono caps labels, a full-screen hamburger menu at every width) with Lorde's own
identity: indigo accent, concentric-ring mark, original SVG art and copy. Pass/reject use the Okabe-Ito palette so
they stay distinguishable with colour-vision deficiencies. Reduced-motion users get no animation.

`tests/unit/vectors.test.ts` checks the TypeScript copy of CuratorKIT's parser against vectors recorded from the
Python package; `tests/unit/keys.test.ts` checks key hashing against the Gateway.
