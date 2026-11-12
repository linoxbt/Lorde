/**
 * Pagination helpers shared by the data layer and the list pages (pure: unit-tested in tests/unit/paging.test.ts).
 *
 * A page past the end is not an error: PostgREST answers a `range()` beyond the last row with 416 / PGRST103
 * ("Requested range not satisfiable"); the data layer turns that into an empty page with the real total, and the page
 * shows "past the end" with a link back to the last page instead of the error screen.
 */
export const MAX_PAGE = 100_000;

/** Zero-based page index from a 1-based `?page=` value; junk, zero and negatives give the first page. */
export function parsePage(raw: unknown, max = MAX_PAGE): number {
  const s = Array.isArray(raw) ? raw[0] : raw;
  const n = typeof s === "string" ? Number.parseInt(s, 10) : typeof s === "number" ? Math.trunc(s) : NaN;
  return Math.max(1, Math.min(max, Number.isFinite(n) ? n : 1)) - 1;
}

/** The PostgREST error for an offset past the last row (HTTP 416). */
export function isRangePastEnd(err: { code?: string; status?: number } | null | undefined): boolean {
  return Boolean(err && (err.code === "PGRST103" || err.status === 416));
}

export const pageCount = (total: number, size: number) => Math.max(1, Math.ceil(total / size));

/** `page` (zero-based) lies past the last page of a non-empty list. */
export const isPastEnd = (page: number, total: number, size: number) => total > 0 && page >= pageCount(total, size);
