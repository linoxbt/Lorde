/** Shown inside the Console shell while a screen's data loads. */
export default function Loading() {
  return (
    <div role="status" aria-label="Loading" className="animate-pulse space-y-6">
      <div className="h-8 w-48 rounded-lg bg-wash-2" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => <div key={i} className="h-28 rounded-[var(--radius-card)] bg-wash" />)}
      </div>
      <div className="h-64 rounded-[var(--radius-card)] bg-wash" />
      <span className="sr-only">Loading…</span>
    </div>
  );
}
