# Lorde staging deployment

Deployed 10 Nov 2026 from branch `ccr-e4bea25f-7bzaqy`.

| Piece | Where | URL / id |
|---|---|---|
| Console + website | Vercel, team Linoxbt, project `lorde-console` | https://lorde-console.vercel.app |
| Gateway + Job Worker (one service, `infra/Dockerfile.api`) | Railway project `lorde`, service `lorde-api` | https://lorde-api-production.up.railway.app |
| Auth + Postgres (accounts, orgs, keys, jobs, judgments, usage, audit) | Supabase project `lorde` (us-east-1), ref `kmizhjblteiqsuwzbgvt`; the Gateway connects through the session pooler (`aws-1-us-east-1.pooler.supabase.com:5432`) | https://kmizhjblteiqsuwzbgvt.supabase.co |
| Redis (rate limits) | Railway `Redis` | private network |
| Dataset/result storage | Railway volume `lorde-data` at `/data` | served via signed Gateway URLs |
| Model (staging Serve) | Railway `lorde-serve`: llama.cpp `ghcr.io/ggml-org/llama.cpp:server`, CPU, private network only, volume `lorde-models` | `http://lorde-serve.railway.internal:8080/v1` |

Accounts (11 Nov): Supabase project `lorde` in a second Supabase account (the first is at the free plan's two-project
limit). Migrations 0001-0004 were applied through the Management API; the Gateway moved from Railway Postgres to it
(`LORDE_DB_SHIM=0`) and re-created the bootstrap org and admin key there. Auth settings: site URL
https://lorde-console.vercel.app, redirect allow-list `https://lorde-console.vercel.app/**`, email confirmation on,
passwords of 8+ characters. GitHub sign-in needs an OAuth app (callback
`https://kmizhjblteiqsuwzbgvt.supabase.co/auth/v1/callback`) entered under Authentication → Providers → GitHub.
Supabase's built-in mailer allows only a few emails per hour; add custom SMTP before real traffic. The Railway
`Postgres` service is no longer used.

Verified live with a temporary confirmed account (deleted afterwards): `/console/*` while signed out → `/signin`;
wrong password → "Wrong email or password."; sign-in → the account's own organization (no demo banner); a key created in
the Console authenticates at the Gateway (200); `/signin` while signed in → `/console`; sign-out → `/signin`. Keys
revoked in the Console stop working at the Gateway within its 60 s lookup cache.

## What was verified live

- `/healthz` 200; migrations 0001-0004 applied on boot; bootstrap org "Lorde staging" with one admin key.
- Auth: no key → 401; the admin key authenticates against Postgres (peppered hash).
- `/v1/judge/*` → 503 `model_unavailable` until the model endpoint exists (expected); `/readyz` 503 for the same reason.
- `/v1/uploads` + signed PUT to the volume works; `/v1/usage` reads Postgres.
- CORS allows the Console origin; every Console page returns 200.
- With the CPU staging model: `/readyz` 200 (model loaded ~40 s after deploy); `/status` "Gateway and model server responding" (the page now names the served revision and warns while it is the untrained base model).
- Live grounding judgment through the Gateway: 26 s, schema-valid reply under `curatorkit-0.1.23/hallucination@027e5efc`.
  The untrained base model listed "about 6,650 km" as unsupported yet scored 0.8 (pass): the miscalibration Lorde is
  trained to remove, and a ready "before" example for the article.
- Live reward judgment through the Vercel Console proxy (`/api/gateway`, caller's own key): 17 s, "Test it." → 0.35, reject.

## Secrets

- `LORDE_KEY_PEPPER` was generated in the build session and written to Railway with the API directly (never echoed).
- The admin API key (`lorde_sk_s0GVb8vj…`) exists only as a hash on Railway. Its plaintext was handed to the owner
  once; store it in a password manager. Create more keys with `python -m gateway.keys` against `DATABASE_URL`, or
  rotate by adding a new bootstrap entry.

## Staging model (no GPU, no card)

The Hugging Face account cannot pay for Inference Endpoints, and Docker Spaces now need PRO, so staging serves
Cohere's official, ungated `CohereLabs/tiny-aya-global-GGUF` (Q4_K_M) with llama.cpp on Railway CPU. It has no
public domain: only the Gateway reaches it over Railway's private network, with an API key (`LLAMA_API_KEY` there,
`VLLM_API_KEY` on the Gateway, generated in the build session and never displayed). The Gateway labels it
`tiny-aya-global-q4km@staging-base-cpu`: this is the **untrained base model**, not Lorde, and a judgment takes tens of
seconds (`LORDE_UPSTREAM_TIMEOUT=240`, `LORDE_UPSTREAM_CONCURRENCY=1`). Configuration is entirely llama.cpp
environment variables (`LLAMA_ARG_HF_REPO`, `LLAMA_ARG_HOST=::`, `LLAMA_ARG_CTX_SIZE=4096`, `LLAMA_ARG_JINJA`,
`LLAMA_ARG_ALIAS=lorde`). To serve the trained Lorde on CPU, export a GGUF from `runs/lorde-v1/merged`, push it to
the Hub, and set `LLAMA_ARG_HF_REPO` to it.

## Connect a GPU model (when billing works)

1. Hugging Face → Inference Endpoints → New: model `CohereLabs/tiny-aya-global` for staging, or your trained
   `<you>/lorde-tiny-aya` at revision `v1.0.0`; engine **vLLM**; **AWS us-east-1, 1× NVIDIA L4**; security
   **Protected**; scale-to-zero after 15 min; container arguments
   `--served-model-name lorde --max-model-len 4096`.
2. Railway → `lorde` → `lorde-api` → Variables:
   - `VLLM_BASE_URL` = `https://<endpoint-host>/v1`
   - `VLLM_API_KEY` = a Hugging Face token allowed to call the endpoint (mark it sealed)
   - `LORDE_MODEL_REPO` / `LORDE_MODEL_REVISION` = what the endpoint serves (e.g. `tiny-aya-global` / `main` for
     staging, `lorde-tiny-aya` / `v1.0.0` after training)
3. `/status` turns green and the playground judges live with your key.

## Hardening after the audit (11 Nov)

Migrations `0005_hardening.sql` and `0006_console.sql` were applied by the Gateway's migrator on boot (0006 at 07:01,
0005 at 16:12 UTC; the reverse order was replayed locally and gives the same end state). Verified live:

- Database (as a signed-in user through PostgREST): inserting `datasets` → 403; `api_keys.rpm` above 6000 or an unknown
  scope → 23514; changing `rpm` → 403; revoking works and un-revoking is refused; an unconfirmed sign-up gets no
  organization, confirming creates exactly one; `usage_daily` works for members and is refused for `anon`.
- Gateway: `/docs`, `/redoc`, `/openapi.json` 404 (enable with `LORDE_PUBLIC_DOCS=1`); `/metrics` 404 without
  `LORDE_METRICS_TOKEN`; `/readyz` reports serve, database and Redis; CORS allows PUT from the Console; a 6 MB body
  without a key → 401 before it is read; malformed keys are refused before any lookup; `n != 1` → 400.
- End to end: a 5.9 MB upload (refused before), a gating job through the worker (succeeded in 47 s), result download,
  `judgments.lang` recorded, the job metered as `usage_events.source = 'job'`, `Idempotency-Key` returns the same job.
- Console: signed-out redirects keep the query string; `/api/gateway/*`, `/api/judge`, `/api/keys` → 401 without a
  session; injected `?error=` text is not shown; every Console page renders real data (no demo banner); a key created in
  the Console authenticates at the Gateway; owner data deletion returns real counts and expires the files for the
  6-hourly purge.

New Gateway settings (defaults): `LORDE_PUBLIC_DOCS=0`, `LORDE_ACCESS_LOG=1`, `LORDE_MODEL_REGISTRY=1`,
`LORDE_JOB_MAX_SECONDS=3600`, `LORDE_JOB_MAX_ROWS=50000`, `LORDE_JOB_MAX_INPUT_BYTES=209715200`,
`LORDE_WORKER_JUDGE_CONCURRENCY=1`, `LORDE_WORKER_SHARES_UPSTREAM=1`, `LORDE_ORG_UPSTREAM_CONCURRENCY=1`,
`LORDE_UPSTREAM_QUEUE_TIMEOUT_S=30`, `LORDE_UPSTREAM_MAX_WAITERS=32`, `LORDE_ORG_DAILY_JUDGMENTS=5000`, `LORDE_IP_RPM=300`,
`LORDE_TRUSTED_PROXIES` (empty), `LORDE_RELAY_SECRET` (unset). Set in production (CPU llama.cpp, ~20 s per judgment,
about 4,300 judgments a day in total): `LORDE_METRICS_TOKEN`, `LORDE_UPSTREAM_QUEUE_TIMEOUT_S=60`,
`LORDE_UPSTREAM_MAX_WAITERS=4`, `LORDE_ORG_DAILY_JUDGMENTS=500`, `LORDE_IP_RPM=300`, `LORDE_WORKER_JUDGE_CONCURRENCY=1`,
and `LORDE_RELAY_SECRET` (same value on Vercel, so the Console relay passes each user's IP). Migration
`0007_fairness.sql` adds round-robin job claiming across organizations.
Promote a model with `python -m gateway.models_cli promote <repo> <revision>`.

Label service (`lorde-label`, `infra/Dockerfile.label`): `LABEL_HUB_REPO=Oxlino/lorde-work`, `LABEL_PASSWORD`,
`LABEL_USER=lorde` (login name, recorded as the labeller), `HF_TOKEN` (should be a fine-grained token with write
access to `datasets/Oxlino/lorde-work` only), optional `LABEL_USERS=name:pw,...`. The hub sample was blinded with
`forge/04b_blind_sample.py` (opaque ids, no variants; the key file stays private).

Vercel adds `NEXT_PUBLIC_SITE_URL=https://lorde-console.vercel.app`.

Still to do by the account owner: apply the email templates below (cross-device email links); custom SMTP; CAPTCHA
(Turnstile, below); leaked-password protection needs the Supabase Pro plan; the unused Railway `Postgres` service can
be removed.

## Supabase Auth: email templates

The default templates link to `{{ .ConfirmationURL }}`, a PKCE link: it only works in the browser that asked for the
email, so a link opened in a phone's mail app or on another device fails (`pkce_code_verifier_not_found`). The five
templates in `supabase/templates/` link to `{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=<type>&next={{ .RedirectTo }}`
instead, which the Console verifies server-side (`verifyOtp`), so they work anywhere. Apply them once per project:

1. Supabase Dashboard → project `lorde` → Authentication → URL Configuration: Site URL
   `https://lorde-console.vercel.app` (it is `{{ .SiteURL }}` in the links), redirect allow-list
   `https://lorde-console.vercel.app/**`.
2. Authentication → Emails → Templates (older dashboards: Authentication → Email Templates). For each template,
   set the subject and paste the whole file as the message body (Source view), then Save:

   | Template | Subject | File | `type` |
   |---|---|---|---|
   | Confirm sign up | `Confirm your Lorde account` | `supabase/templates/confirm-signup.html` | `email` |
   | Invite user | `You have been invited to Lorde` | `supabase/templates/invite.html` | `invite` |
   | Magic link | `Your Lorde sign-in link` | `supabase/templates/magic-link.html` | `magiclink` |
   | Change email address | `Confirm your new email for Lorde` | `supabase/templates/email-change.html` | `email_change` |
   | Reset password | `Reset your Lorde password` | `supabase/templates/recovery.html` | `recovery` |

   The subject is also in the comment at the top of each file. Leave "Reauthentication" as is (the Console does
   not use it).
3. Check: sign up on one browser, open the confirmation email on another device; it must land in the Console signed in.
   Request a password reset and open it elsewhere; it must land on "Choose a new password".

`/auth/confirm` accepts `type` = `signup`, `email`, `magiclink`, `recovery`, `email_change` and `invite` (unit test
`tests/unit/auth.test.ts` renders each template and checks it). Recovery always lands on `/reset-password`, an invite on
`/reset-password?invited=1` (choose a first password); the others follow `next`, limited to `/console/*`. Until the
templates are applied, the old PKCE links still work in the browser that requested them; opened elsewhere, the sign-in
page says "Your email is confirmed: sign in to continue" (sign-up) or that the link was opened in a different browser
and to request a new one (sign-in link, password reset), instead of "We could not start your session".

## Supabase Auth: CAPTCHA (Cloudflare Turnstile)

Optional bot protection on sign-up, password sign-in, email-link sign-in, "resend confirmation" and password-reset
requests (audit S1/S12: open sign-up is otherwise free to script). The Console shows the Turnstile widget and passes
its single-use token to supabase-js (`options.captchaToken`) only when `NEXT_PUBLIC_TURNSTILE_SITE_KEY` is set;
without it nothing is loaded and nothing changes. Email links (`/auth/confirm`) and GitHub sign-in need no token.

1. Cloudflare dashboard → Turnstile → Add widget: hostnames `lorde-console.vercel.app` (add `localhost` for local
   testing), widget mode **Managed**. Copy the site key and the secret key.
2. Vercel → `lorde-console` → Settings → Environment Variables: `NEXT_PUBLIC_TURNSTILE_SITE_KEY` = the site key
   (Production and Preview), then redeploy: it is inlined at build time. Deploy this **before** step 3, or sign-ins
   fail for lack of a token.
3. Supabase Dashboard → project `lorde` → Authentication → Attack Protection (older dashboards: Authentication →
   Settings → Bot and Abuse Protection) → Enable CAPTCHA protection → provider **Turnstile by Cloudflare** → paste
   the secret key → Save.
4. Check: sign in on the live site (the widget appears above the button); with the widget blocked, a sign-in attempt
   must say "Complete the security check above the button first."

To turn it off, disable CAPTCHA in Supabase first, then remove the variable and redeploy. The CSP
(`console/lib/csp.ts`) adds `https://challenges.cloudflare.com` to `script-src`, `frame-src` and `connect-src` only
while the site key is set. `pnpm test:e2e:live` builds with Cloudflare's test site key and a stand-in Turnstile script.
