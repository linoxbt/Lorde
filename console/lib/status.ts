/**
 * Wording of the public status page. Pure (no imports), so it is unit-tested directly.
 *
 * The page only probes the Gateway's /healthz and /readyz, so the summary never claims more than that:
 * it says the Gateway and the model server respond, not that "all systems" work, and "unknown" when a check got no
 * answer. The served model is shown
 * when the Gateway reports it in those responses, else from NEXT_PUBLIC_LORDE_MODEL_REVISION, else not at all.
 */

/** "unknown": the Console could not get an answer to that check (timeout, unreadable reply), so it claims nothing. */
export type CheckState = "ok" | "degraded" | "down" | "unknown" | "demo";
export type Check = { name: string; state: CheckState; detail: string; ms?: number };
export type Summary = { state: CheckState; label: string };
export type ServedModel = {
  /** e.g. "lorde-tiny-aya@v1.0.0" or "tiny-aya-global-q4km@staging-base-cpu" */
  label: string;
  revision: string;
  /** "gateway": reported by the Gateway's health endpoints; "config": the Console's configured value */
  source: "gateway" | "config";
  /** the revision names the untrained base model (staging) */
  base: boolean;
  notice?: string;
};

export const BASE_NOTICE = "Serving the untrained base model (staging), not the trained Lorde judge.";

export function summarize(checks: Check[]): Summary {
  if (checks.length && checks.every((c) => c.state === "demo")) return { state: "demo", label: "Demo mode" };
  const gateway = checks.find((c) => c.name === "Gateway");
  if (!gateway || gateway.state === "down") return { state: "down", label: "Gateway unreachable" };
  if (gateway.state === "unknown") return { state: "unknown", label: "Status unknown: the Gateway did not answer the check" };
  if (checks.every((c) => c.state === "ok")) return { state: "ok", label: "Gateway and model server responding" };
  const bad = checks.filter((c) => c.state === "degraded" || c.state === "down");
  if (!bad.length) return { state: "unknown", label: "Gateway responding; model server status unknown" };
  if (bad.every((c) => c.name === "Lorde Serve")) return { state: "degraded", label: "Gateway responding; model server not ready" };
  return { state: "degraded", label: `Gateway responding; ${bad.map((c) => c.name.toLowerCase().replace("lorde serve", "model server")).join(" and ")} not ready` };
}

type Probe = { ok: boolean; status: number | null; body: unknown; ms: number };

/**
 * The checks from the Gateway's public /healthz and /readyz. A probe that got no answer (status null: timeout or
 * network error) or an unreadable /readyz reply is "unknown", never "degraded": only an answer can say not ready.
 * /readyz reports the model server, the database and Redis; the job worker is not probed, so nothing is claimed for it.
 */
export function gatewayChecks(health: Probe, ready: Probe): Check[] {
  const gateway: Check = health.status === null
    ? { name: "Gateway", state: "down", detail: "Unreachable", ms: health.ms }
    : health.ok ? { name: "Gateway", state: "ok", detail: "Responding", ms: health.ms }
      : { name: "Gateway", state: "degraded", detail: `Answering with HTTP ${health.status}`, ms: health.ms };
  const r = ready.body && typeof ready.body === "object" ? (ready.body as Record<string, unknown>) : null;
  if (ready.status === null || !r || (gateway.state === "down")) {
    return [gateway, { name: "Lorde Serve", state: "unknown", detail: "Could not check: the Gateway's readiness check did not answer", ms: ready.status === null ? undefined : ready.ms }];
  }
  const serveOk = typeof r.serve === "boolean" ? r.serve && r.breaker_open !== true : ready.ok;
  const serve: Check = serveOk
    ? { name: "Lorde Serve", state: "ok", detail: "Model server responding", ms: ready.ms }
    : { name: "Lorde Serve", state: "degraded", detail: r.breaker_open === true ? "Circuit open after model server errors" : "Model server not ready", ms: ready.ms };
  const checks = [gateway, serve];
  if (typeof r.database === "boolean") {
    checks.push(r.database ? { name: "Database", state: "ok", detail: "Responding" } : { name: "Database", state: "down", detail: "Unreachable from the Gateway" });
  }
  return checks;
}

/** Share one result for `ttlMs` (and one in-flight call), so a busy /status page costs the Gateway two probes per period. */
export function memoize<T>(fn: () => Promise<T>, ttlMs: number, now: () => number = Date.now): () => Promise<T> {
  let at = -Infinity, value: Promise<T> | null = null;
  return () => {
    if (value && now() - at < ttlMs) return value;
    at = now();
    const v = fn();
    value = v;
    v.catch(() => { if (value === v) value = null; });
    return v;
  };
}

/** "base" / "staging-base" in the revision means the untrained base model is served. */
export const isBaseRevision = (revision: string) => /base/i.test(revision);

const str = (o: Record<string, unknown>, ...keys: string[]) => {
  for (const k of keys) {
    const v = o[k];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return undefined;
};

function make(label: string, revision: string, source: ServedModel["source"]): ServedModel {
  const base = isBaseRevision(revision);
  return { label, revision, source, base, ...(base ? { notice: BASE_NOTICE } : {}) };
}

/** The served model from the Gateway's health bodies (if they report one), else the configured revision. */
export function servedModel(bodies: unknown[], configured?: string): ServedModel | null {
  for (const b of bodies) {
    if (!b || typeof b !== "object") continue;
    const o = b as Record<string, unknown>;
    const nested = o.model && typeof o.model === "object" ? (o.model as Record<string, unknown>) : null;
    const src = nested ?? o;
    const repo = str(src, "repo", "model_repo");
    const revision = str(src, "revision", "model_revision");
    const name = nested ? str(nested, "model", "label", "id") : str(o, "model", "model_label");
    const label = name ?? (repo && revision ? `${repo.split("/").pop()}@${revision}` : revision);
    if (label) return make(label, revision ?? label.split("@").pop() ?? label, "gateway");
  }
  const c = configured?.trim();
  return c ? make(c, c.split("@").pop() ?? c, "config") : null;
}
