/**
 * Friendly text for Gateway error envelopes ({error: {code, message}}), shared by the playground, datasets,
 * jobs and review screens. Pure (no imports), so it is unit-tested directly.
 */

export type ErrorBody = { error?: { code?: string; message?: string } } | null | undefined;

function wait(retryAfter?: string | null): string | null {
  if (!retryAfter || !/^\d+$/.test(retryAfter.trim())) return null;
  const s = Number(retryAfter);
  if (s < 120) return `${Math.max(1, s)} s`;
  if (s < 7200) return `${Math.ceil(s / 60)} min`;
  return `${Math.round(s / 3600)} h`;
}

const sentence = (m: string) => {
  const t = m.trim().replace(/[.\s]+$/, "");
  return t ? `${t[0]!.toUpperCase()}${t.slice(1)}.` : "";
};

export function friendlyGatewayError(status: number, body: ErrorBody, retryAfter?: string | null,
  fallback?: string): string {
  const code = body?.error?.code;
  const message = body?.error?.message ?? "";
  const after = wait(retryAfter);
  if (code === "quota_exceeded") {
    return "Your organization has used today's judgment quota." +
      (after ? ` It resets in about ${after}.` : " It resets daily.") +
      " Ask the operator if you need a higher quota.";
  }
  if (code === "rate_limited" && /model busy/i.test(message)) {
    return `Lorde is busy with other requests right now. Try again in ${after ?? "a few seconds"}.`;
  }
  if (code === "rate_limited") {
    return `${sentence(message) || "Too many requests."} Try again in ${after ?? "a moment"}.`;
  }
  return message || fallback || `Request failed (${status})`;
}
