import type { UsageDay } from "./types";

/**
 * "This week" is the last 7 calendar days (today included) and "last week" the 7 before, from per-day rows.
 * `today` defaults to the last day of the series (usage_daily always ends today, UTC).
 */
export function weekTotals(days: UsageDay[], today = days.at(-1)?.day ?? new Date().toISOString().slice(0, 10)) {
  const t = Date.parse(`${today}T00:00:00Z`);
  const age = (d: string) => Math.round((t - Date.parse(`${d}T00:00:00Z`)) / 86400_000);
  let week = 0, prev = 0;
  for (const d of days) {
    const a = age(d.day);
    if (a >= 0 && a < 7) week += d.judgments;
    else if (a >= 7 && a < 14) prev += d.judgments;
  }
  return { week, prev };
}

/** Compact axis labels that stay readable for small values: 0, 5, 40, 950, 1.2k, 12k, 1.5M. */
export function compact(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e6) return `${+(v / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
  if (a >= 1e3) return `${+(v / 1e3).toFixed(a >= 1e4 ? 0 : 1)}k`;
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}
