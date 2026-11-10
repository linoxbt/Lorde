/** Decision agreement and Cohen's kappa at a threshold (same definitions as lorde_forge.metrics). */
export function agreement(pairs: { human: number; lorde: number }[], t = 0.7) {
  const n = pairs.length;
  if (!n) return { n, agreement: null as number | null, kappa: null as number | null };
  const a = pairs.map((p) => p.human >= t), b = pairs.map((p) => p.lorde >= t);
  const po = a.filter((x, i) => x === b[i]).length / n;
  const pa = a.filter(Boolean).length / n, pb = b.filter(Boolean).length / n;
  const pe = pa * pb + (1 - pa) * (1 - pb);
  const kappa = pe >= 1 ? (po === 1 ? 1 : 0) : (po - pe) / (1 - pe);
  return { n, agreement: po, kappa };
}
