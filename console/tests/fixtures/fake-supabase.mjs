// A small fake of the Supabase Auth and PostgREST endpoints the Console calls, for the live-mode e2e tests
// (tests/e2e-live). It records calls so tests can check what the Console sent. Never used in production.
//
//   node tests/fixtures/fake-supabase.mjs [port]      (default 54399)
//
// Users: "noorg" (signed in, member of no organization). Tokens are unsigned HS256-shaped JWTs; with HS256
// the Supabase client verifies them by calling GET /auth/v1/user, which this server answers.
import { createServer } from "node:http";

const port = Number(process.argv[2] ?? process.env.FAKE_SUPABASE_PORT ?? 54399);
const USERS = { noorg: { id: "00000000-0000-4000-8000-000000000001", email: "noorg@example.com" } };
const calls = [];

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
  if (path === "/auth/v1/resend") return send(200, {});
  if (path === "/auth/v1/logout") return send(204);
  if (path === "/auth/v1/signup") {
    return send(200, { id: "new-user", email: json?.email, aud: "authenticated", role: "", identities: [{ id: "i1" }],
      created_at: "2026-11-11T00:00:00Z", app_metadata: {}, user_metadata: {} });
  }
  if (path.startsWith("/auth/v1/.well-known/jwks.json")) return send(200, { keys: [] });
  if (path.startsWith("/rest/v1/")) {
    // Every table is empty for these users (in particular org_members: no organization).
    const single = (req.headers.accept ?? "").includes("vnd.pgrst.object");
    if (single) return send(406, { code: "PGRST116", message: "JSON object requested, multiple (or no) rows returned", details: "0 rows", hint: null });
    return send(200, [], { "content-range": "*/0" });
  }
  return send(404, { msg: "not found in fake Supabase" });
});

server.listen(port, "127.0.0.1", () => console.log(`fake Supabase on http://127.0.0.1:${port}`));
