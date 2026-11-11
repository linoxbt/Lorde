/**
 * Wording of the public status page. Pure (no imports), so it is unit-tested directly.
 *
 * The page only probes the Gateway's /healthz and /readyz, so the summary never claims more than that:
 * it says the Gateway and the model server respond, not that "all systems" work. The served model is shown
 * when the Gateway reports it in those responses, else from NEXT_PUBLIC_LORDE_MODEL_REVISION, else not at all.
 */

export type CheckState = "ok" | "degraded" | "down" | "demo";
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
  if (checks.every((c) => c.state === "ok")) return { state: "ok", label: "Gateway and model server responding" };
  return { state: "degraded", label: "Gateway responding; model server not ready" };
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
