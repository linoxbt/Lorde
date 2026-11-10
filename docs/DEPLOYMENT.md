# Lorde staging deployment

Deployed 10 Nov 2026 from branch `ccr-e4bea25f-7bzaqy`.

| Piece | Where | URL / id |
|---|---|---|
| Console + website | Vercel, team Linoxbt, project `lorde-console` | https://lorde-console.vercel.app |
| Gateway + Job Worker (one service, `infra/Dockerfile.api`) | Railway project `lorde`, service `lorde-api` | https://lorde-api-production.up.railway.app |
| Postgres (keys, jobs, judgments, usage, audit) | Railway `Postgres` (Supabase shim, `LORDE_DB_SHIM=1`) | private network |
| Redis (rate limits) | Railway `Redis` | private network |
| Dataset/result storage | Railway volume `lorde-data` at `/data` | served via signed Gateway URLs |
| Model (staging Serve) | Railway `lorde-serve`: llama.cpp `ghcr.io/ggml-org/llama.cpp:server`, CPU, private network only, volume `lorde-models` | `http://lorde-serve.railway.internal:8080/v1` |

Supabase was skipped: the organization is at the free plan's two-project limit. Without it the Console has no
sign-in and shows demo data; the status page and the playground (with your own API key) use the live Gateway.

## What was verified live

- `/healthz` 200; migrations 0001-0004 applied on boot; bootstrap org "Lorde staging" with one admin key.
- Auth: no key → 401; the admin key authenticates against Postgres (peppered hash).
- `/v1/judge/*` → 503 `model_unavailable` until the model endpoint exists (expected); `/readyz` 503 for the same reason.
- `/v1/uploads` + signed PUT to the volume works; `/v1/usage` reads Postgres.
- CORS allows the Console origin; every Console page returns 200; `/status` shows Gateway OK, model degraded.

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
