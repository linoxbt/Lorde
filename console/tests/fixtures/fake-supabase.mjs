// A small fake of the Supabase Auth and PostgREST endpoints the Console calls, for the live-mode e2e tests
// (tests/e2e-live). It records calls so tests can check what the Console sent. Never used in production.
//
//   node tests/fixtures/fake-supabase.mjs [port]      (default 54399)
//
// Users: "noorg" (signed in, member of no organization) and "member" (owner of the organization "Acme Research",
// with jobs, datasets, API keys and usage, so the Console's data layer runs against realistic rows). Tokens are
// unsigned HS256-shaped JWTs; with HS256 the Supabase client verifies them by calling GET /auth/v1/user, which this
// server answers. PostgREST is emulated closely enough for the Console's queries: eq/is filters, order, offset/limit
// with exact counts (Content-Range), HEAD counts, 416 PGRST103 past the last row, single/maybeSingle, embedded
// datasets(name) on jobs, inserts with return=representation, and the RPCs from 0006_console.sql.
import { createServer } from "node:http";

const port = Number(process.argv[2] ?? process.env.FAKE_SUPABASE_PORT ?? 54399);
const USERS = {
  noorg: { id: "00000000-0000-4000-8000-000000000001", email: "noorg@example.com" },
  member: { id: "00000000-0000-4000-8000-000000000002", email: "member@example.com" },
};
const calls = [];

// ---- The member's organization ------------------------------------------------------------------------------
const ORG = "10000000-0000-4000-8000-000000000001";
const NOW = Date.now();
const hoursAgo = (h) => new Date(NOW - h * 3600_000).toISOString();
const uid = (prefix, n) => `${prefix}0000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const DATASETS = [
  { id: uid("3", 1), org_id: ORG, name: "support-answers.jsonl", storage_path: `datasets/${ORG}/${uid("3", 1)}/support-answers.jsonl`,
    format: "jsonl", rows: null, bytes: 2_400_000, created_at: hoursAgo(5), expires_at: hoursAgo(-24 * 29) },
  { id: uid("3", 2), org_id: ORG, name: "policy-qa.parquet", storage_path: `datasets/${ORG}/${uid("3", 2)}/policy-qa.parquet`,
    format: "parquet", rows: null, bytes: 18_000_000, created_at: hoursAgo(30), expires_at: hoursAgo(-24 * 28) },
  // past its retention, waiting for the purge: listed, never selectable
  { id: uid("3", 3), org_id: ORG, name: "old-batch.csv", storage_path: `datasets/${ORG}/${uid("3", 3)}/old-batch.csv`,
    format: "csv", rows: null, bytes: 700_000, created_at: hoursAgo(24 * 31), expires_at: hoursAgo(2) },
];
const STATUSES = ["running", "succeeded", "succeeded", "failed", "succeeded", "cancelled"];
const JOBS = Array.from({ length: 30 }, (_, i) => {
  const status = STATUSES[i % STATUSES.length];
  const ds = DATASETS[i % 2];
  const done = status === "succeeded";
  const rows = 400 + i * 10, passed = done ? Math.round(rows * 0.7) : undefined;
  return {
    id: uid("2", i + 1), org_id: ORG, status, dataset_id: ds.id, model_revision: "lorde-tiny-aya@v1.0.0",
    config: { gates: { grounding: 0.7, reward: i % 3 ? 0.7 : null }, options: { pii: false, dedup: "exact" },
      source: { storage_path: ds.storage_path, format: ds.format } },
    counts: done ? { rows_in: rows, evaluated: rows, passed, rejected: rows - passed, pass_rate: passed / rows, rejection_reasons: { unsupported_claim: rows - passed } } : {},
    error: status === "failed" ? "worker: dataset file not found" : null, cancel_requested: false,
    created_at: hoursAgo(i * 3 + 1), started_at: status === "queued" ? null : hoursAgo(i * 3 + 0.9),
    finished_at: done || status === "failed" || status === "cancelled" ? hoursAgo(i * 3 + 0.5) : null,
  };
});
// a job whose upload was purged since (dataset_id set null), and one on a Hub dataset
JOBS[28] = { ...JOBS[28], dataset_id: null, config: { ...JOBS[28].config, source: { storage_path: `datasets/${ORG}/${uid("3", 9)}/purged-batch.jsonl`, format: "jsonl" } } };
JOBS[29] = { ...JOBS[29], dataset_id: null, config: { ...JOBS[29].config, source: { dataset_url: "hf://datasets/acme/chat-pairs" } } };
const KEYS = [
  { id: uid("4", 1), org_id: ORG, name: "ci-pipeline", prefix: "lorde_sk_Ab12Cd", scopes: ["judge", "jobs:read", "jobs:write", "usage:read"],
    rpm: 600, created_at: hoursAgo(72), last_used_at: hoursAgo(1), revoked_at: null },
  { id: uid("4", 2), org_id: ORG, name: "old-laptop", prefix: "lorde_sk_Zz98Yx", scopes: ["judge"],
    rpm: 60, created_at: hoursAgo(400), last_used_at: hoursAgo(300), revoked_at: hoursAgo(200) },
];
const TABLES = {
  org_members: [{ org_id: ORG, user_id: USERS.member.id, role: "owner", created_at: hoursAgo(24 * 40) }],
  orgs: [{ id: ORG, name: "Acme Research", retention_days: 30, store_content: false }],
  model_versions: [],
  datasets: DATASETS,
  jobs: JOBS,
  api_keys: KEYS,
  judgments: [],
  human_labels: [],
};
const RPCS = {
  org_members_with_email: () => [{ user_id: USERS.member.id, email: USERS.member.email, role: "owner", created_at: hoursAgo(24 * 40) }],
  usage_daily: (a) => Array.from({ length: a.days ?? 14 }, (_, i) => ({ day: hoursAgo(24 * ((a.days ?? 14) - 1 - i)).slice(0, 10),
    judgments: 100 + i * 7, calls: 20 + i, prompt_tokens: 50_000 + i * 900, completion_tokens: 4_000 + i * 80 })),
  gate_pass_rates: () => [{ gate: "grounding", judged: 4200, passed: 3010 }, { gate: "reward", judged: 3900, passed: 2950 }],
  job_judgment_stats: () => [],
  review_queue: () => [],
  my_review_labels: () => [],
};
const isMember = (user, orgId) => user && TABLES.org_members.some((m) => m.user_id === user.id && m.org_id === orgId);

/** Rows of `table` visible to `user` (a stand-in for RLS) that match the PostgREST eq/is filters in `q`. */
function query(table, q, user) {
  let rows = (TABLES[table] ?? []).filter((r) => (table === "org_members" ? r.user_id === user?.id
    : table === "orgs" ? isMember(user, r.id) : isMember(user, r.org_id)));
  for (const [k, v] of q) {
    if (["select", "order", "offset", "limit", "columns", "on_conflict"].includes(k)) continue;
    if (v.startsWith("eq.")) rows = rows.filter((r) => String(r[k]) === v.slice(3));
    else if (v === "is.null") rows = rows.filter((r) => r[k] === null || r[k] === undefined);
  }
  if (table === "jobs") rows = rows.map((j) => ({ ...j, datasets: j.dataset_id ? { name: DATASETS.find((d) => d.id === j.dataset_id)?.name ?? null } : null }));
  return rows;  // every table is already newest first, as the Console orders it
}

const b64url = (s) => Buffer.from(s).toString("base64url");
function tokenFor(name, ttl = 24 * 3600) {
  const u = USERS[name];
  const now = Math.floor(Date.now() / 1000);
  const header = b64url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = b64url(JSON.stringify({ sub: u.id, email: u.email, aud: "authenticated", role: "authenticated", exp: now + ttl, iat: now }));
  return `${header}.${payload}.${b64url("fake-signature")}`;
}
const userJson = (u) => ({ id: u.id, aud: "authenticated", role: "authenticated", email: u.email, email_confirmed_at: "2026-11-01T00:00:00Z",
  app_metadata: { provider: "email" }, user_metadata: {}, identities: [], created_at: "2026-11-01T00:00:00Z", updated_at: "2026-11-01T00:00:00Z" });
const session = (name) => ({ access_token: tokenFor(name), token_type: "bearer", expires_in: 86400,
  expires_at: Math.floor(Date.now() / 1000) + 86400, refresh_token: `refresh-${name}`, user: userJson(USERS[name]) });

function userFromAuth(req) {
  const m = /^Bearer (.+)$/.exec(req.headers.authorization ?? "");
  if (!m) return null;
  try {
    const payload = JSON.parse(Buffer.from(m[1].split(".")[1] ?? "", "base64url").toString());
    return Object.values(USERS).find((u) => u.id === payload.sub && payload.exp > Date.now() / 1000) ?? null;
  } catch {
    return null;
  }
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  let body = "";
  for await (const chunk of req) body += chunk;
  let json = null;
  try { json = body ? JSON.parse(body) : null; } catch { json = null; }
  const send = (status, data, headers = {}) => {
    res.writeHead(status, { "content-type": "application/json", "access-control-allow-origin": "*",
      "access-control-allow-headers": "*", "access-control-allow-methods": "*", ...headers });
    res.end(data === undefined ? "" : JSON.stringify(data));
  };
  if (req.method === "OPTIONS") return send(204);
  const path = url.pathname;
  if (path === "/__calls") return send(200, calls);
  if (path === "/__reset") { calls.length = 0; return send(200, {}); }
  // The cookie @supabase/ssr reads for a signed-in user: sb-<first host label>-auth-token = "base64-" + base64url(session).
  if (path === "/__session") {
    const name = url.searchParams.get("user") ?? "noorg";
    return send(200, { name: `sb-${req.headers.host.split(":")[0].split(".")[0]}-auth-token`,
      value: `base64-${b64url(JSON.stringify(session(name)))}` });
  }
  calls.push({ method: req.method, path, query: Object.fromEntries(url.searchParams), body: json && { ...json, password: json.password ? "***" : undefined } });

  if (path === "/auth/v1/user" && req.method === "GET") {
    const u = userFromAuth(req);
    return u ? send(200, userJson(u)) : send(401, { code: 401, error_code: "bad_jwt", msg: "invalid JWT" });
  }
  if (path === "/auth/v1/token") {
    if (url.searchParams.get("grant_type") === "password") {
      if (json?.email === "unconfirmed@example.com") return send(400, { code: 400, error_code: "email_not_confirmed", msg: "Email not confirmed" });
      return send(400, { code: 400, error_code: "invalid_credentials", msg: "Invalid login credentials" });
    }
    return send(400, { code: 400, error_code: "refresh_token_not_found", msg: "Invalid Refresh Token" });
  }
  if (path === "/auth/v1/verify" && req.method === "POST") {
    if (json?.token_hash === "good-hash") return send(200, session("noorg"));
    return send(403, { code: 403, error_code: "otp_expired", msg: "Email link is invalid or has expired" });
  }
  if (path === "/auth/v1/user" && req.method === "PUT") {
    if (json?.password === "drop-the-connection") return req.socket.destroy();  // a network failure mid-request
    const u = userFromAuth(req);
    return u ? send(200, userJson(u)) : send(401, { code: 401, error_code: "bad_jwt", msg: "invalid JWT" });
  }
  if (path === "/auth/v1/resend" || path === "/auth/v1/recover" || path === "/auth/v1/otp") return send(200, {});
  if (path === "/auth/v1/logout") return send(204);
  if (path === "/auth/v1/signup") {
    return send(200, { id: "new-user", email: json?.email, aud: "authenticated", role: "", identities: [{ id: "i1" }],
      created_at: "2026-11-11T00:00:00Z", app_metadata: {}, user_metadata: {} });
  }
  if (path.startsWith("/auth/v1/.well-known/jwks.json")) return send(200, { keys: [] });
  if (path.startsWith("/rest/v1/")) {
    const user = userFromAuth(req);
    const single = (req.headers.accept ?? "").includes("vnd.pgrst.object");
    const one = (rows) => (rows.length === 1 ? send(200, rows[0])
      : send(406, { code: "PGRST116", message: "JSON object requested, multiple (or no) rows returned", details: `${rows.length} rows`, hint: null }));
    if (path.startsWith("/rest/v1/rpc/")) {
      const fn = RPCS[path.slice("/rest/v1/rpc/".length)];
      const args = json ?? {};
      if (!fn) return send(404, { code: "PGRST202", message: "Could not find the function" });
      return send(200, args.o && !isMember(user, args.o) ? [] : fn(args));
    }
    const table = path.slice("/rest/v1/".length);
    if (req.method === "POST") {  // insert ... select(): echo the row
      const row = { id: `${Date.now()}`, created_at: new Date().toISOString(), ...(Array.isArray(json) ? json[0] : json) };
      return single ? send(201, row) : send(201, [row]);
    }
    const rows = query(table, url.searchParams, user);
    const counted = /count=exact/.test(req.headers.prefer ?? "");
    if (req.method === "HEAD") return send(200, undefined, { "content-range": `*/${rows.length}` });
    if (single) return one(rows);
    const offset = Number(url.searchParams.get("offset") ?? 0), limit = Number(url.searchParams.get("limit") ?? rows.length);
    if (offset > 0 && offset >= rows.length) {
      return send(416, { code: "PGRST103", message: "Requested range not satisfiable",
        details: `An offset of ${offset} was requested, but there are only ${rows.length} rows.`, hint: null }, { "content-range": `*/${rows.length}` });
    }
    const page = rows.slice(offset, offset + limit);
    const range = page.length ? `${offset}-${offset + page.length - 1}` : "*";
    return send(counted && page.length < rows.length ? 206 : 200, page, { "content-range": `${range}/${counted ? rows.length : "*"}` });
  }
  return send(404, { msg: "not found in fake Supabase" });
});

server.listen(port, "127.0.0.1", () => console.log(`fake Supabase on http://127.0.0.1:${port}`));
