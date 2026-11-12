/**
 * Review queue navigation (pure: unit-tested in tests/unit/review.test.ts). Labelled rows are never offered again:
 * "next" skips them, wrapping round once, so a row cannot be saved twice (human_labels has no unique key).
 */
export function nextUnlabelled(refs: readonly string[], labelled: { has(ref: string): boolean }, from: number): number | null {
  for (let k = 1; k <= refs.length; k++) {
    const j = (from + k) % refs.length;
    if (!labelled.has(refs[j]!)) return j;
  }
  return null;
}
