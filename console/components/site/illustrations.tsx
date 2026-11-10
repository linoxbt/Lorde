/* Original line diagrams for the landing page pillars (Lorde palette, no third-party art). */

const A = "var(--color-accent)";
const I = "var(--color-ink)";
const L = "var(--color-line-2)";
const P = "var(--color-pass)";
const R = "var(--color-reject)";

function Frame({ children, label }: { children: React.ReactNode; label: string }) {
  return (
    <svg viewBox="0 0 520 280" role="img" aria-label={label} className="h-full w-full">
      {children}
    </svg>
  );
}

/** CuratorKIT renders the prompt, Lorde replies, CuratorKIT parses: one contract, end to end. */
export function ContractArt() {
  return (
    <Frame label="CuratorKIT renders the prompt and parses Lorde's reply">
      <rect x="40" y="70" width="130" height="140" rx="10" fill="#fff" stroke={I} strokeWidth="1.5" />
      {[95, 112, 129, 146, 163, 180].map((y, i) => (
        <rect key={y} x="58" y={y} width={i % 2 ? 70 : 94} height="6" rx="3" fill={i === 2 ? A : L} />
      ))}
      <text x="105" y="60" textAnchor="middle" fontSize="11" fontFamily="var(--font-mono)" fill="#5b616e">PROMPT</text>
      <path d="M170 140 H220" stroke={I} strokeWidth="1.5" strokeDasharray="4 4" />
      <circle cx="260" cy="140" r="40" fill={A} />
      <circle cx="260" cy="140" r="27" fill="none" stroke="#fff" strokeOpacity=".5" strokeWidth="1.5" />
      <circle cx="260" cy="140" r="14" fill="none" stroke="#fff" strokeOpacity=".85" strokeWidth="1.5" />
      <circle cx="260" cy="140" r="5" fill="#fff" />
      <path d="M300 140 H350" stroke={I} strokeWidth="1.5" strokeDasharray="4 4" />
      <rect x="350" y="70" width="130" height="140" rx="10" fill="#fff" stroke={I} strokeWidth="1.5" />
      <text x="415" y="60" textAnchor="middle" fontSize="11" fontFamily="var(--font-mono)" fill="#5b616e">PARSER</text>
      <text x="366" y="104" fontSize="12" fontFamily="var(--font-mono)" fill={I}>{"{"}</text>
      <text x="378" y="124" fontSize="12" fontFamily="var(--font-mono)" fill={A}>score: 0.92</text>
      <text x="378" y="144" fontSize="12" fontFamily="var(--font-mono)" fill="#5b616e">claims: […]</text>
      <text x="366" y="164" fontSize="12" fontFamily="var(--font-mono)" fill={I}>{"}"}</text>
      <rect x="378" y="178" width="74" height="20" rx="10" fill="var(--color-pass-wash)" />
      <text x="415" y="192" textAnchor="middle" fontSize="11" fontWeight="600" fill="#006b4e">PASS</text>
      {[[30, 40], [490, 240], [480, 40], [36, 236]].map(([x, y]) => (
        <rect key={`${x}${y}`} x={x} y={y} width="6" height="6" fill={A} />
      ))}
    </Frame>
  );
}

/** Structured outputs: every reply lands inside the schema's grid. */
export function SchemaArt() {
  const cells = Array.from({ length: 7 * 12 }, (_, i) => i);
  return (
    <Frame label="Structured outputs keep every reply inside the gate's JSON schema">
      <rect x="110" y="40" width="300" height="200" rx="14" fill="#fff" stroke={I} strokeWidth="1.5" />
      {cells.map((i) => {
        const c = i % 12, r = Math.floor(i / 12);
        const on = (c * 7 + r * 3) % 5 === 0;
        return (
          <rect key={i} x={132 + c * 22} y={62 + r * 24} width="14" height="14" rx="3"
            fill={on ? A : r === 3 && c > 2 && c < 9 ? "var(--color-accent-soft)" : "var(--color-wash-2)"} />
        );
      })}
      <path d="M40 140 C 70 140, 80 90, 110 90" fill="none" stroke={L} strokeWidth="1.5" />
      <path d="M40 140 C 70 140, 80 190, 110 190" fill="none" stroke={L} strokeWidth="1.5" />
      <circle cx="40" cy="140" r="6" fill={I} />
      <path d="M410 140 H470" stroke={I} strokeWidth="1.5" />
      <circle cx="480" cy="140" r="12" fill="none" stroke={P} strokeWidth="2" />
      <path d="M474 140 l4 4 l8 -8" fill="none" stroke={P} strokeWidth="2" />
    </Frame>
  );
}

/** Downstream value: gated data trains better models. */
export function DownstreamArt() {
  const bars = [62, 88, 87];
  const labels = ["UNGATED", "TEACHER", "LORDE"];
  return (
    <Frame label="Models trained on Lorde-gated data match teacher-gated data">
      <line x1="80" y1="230" x2="460" y2="230" stroke={I} strokeWidth="1.5" />
      {bars.map((h, i) => (
        <g key={labels[i]}>
          <rect x={120 + i * 115} y={230 - h * 2} width="70" height={h * 2} rx="8"
            fill={i === 2 ? A : i === 1 ? "var(--color-accent-soft)" : "var(--color-wash-2)"}
            stroke={i === 0 ? L : "none"} />
          <text x={155 + i * 115} y="252" textAnchor="middle" fontSize="11" fontFamily="var(--font-mono)" fill="#5b616e">
            {labels[i]}
          </text>
        </g>
      ))}
      <path d="M155 100 C 220 60, 300 52, 385 48" fill="none" stroke={I} strokeWidth="1.5" strokeDasharray="4 5" />
      <circle cx="385" cy="48" r="5" fill={I} />
      <text x="60" y="40" fontSize="11" fontFamily="var(--font-mono)" fill="#5b616e">F1 · GROUNDEDNESS</text>
      <text x="460" y="40" textAnchor="end" fontSize="10" fontFamily="var(--font-mono)" fill="#8a919e">ILLUSTRATIVE</text>
      <rect x="430" y="60" width="6" height="6" fill={R} />
      <rect x="70" y="200" width="6" height="6" fill={A} />
    </Frame>
  );
}

/** Cost: one small GPU instead of a per-token bill. */
export function CostArt() {
  return (
    <Frame label="One inexpensive GPU instead of paying per token">
      <circle cx="160" cy="140" r="86" fill="none" stroke={L} strokeWidth="1.5" />
      <circle cx="160" cy="140" r="86" fill="none" stroke={A} strokeWidth="10" strokeDasharray="22 520"
        transform="rotate(-90 160 140)" strokeLinecap="round" />
      <circle cx="160" cy="140" r="58" fill="none" stroke={L} strokeWidth="1.5" strokeDasharray="3 6" />
      <text x="160" y="136" textAnchor="middle" fontSize="28" fontWeight="600" fill={I} letterSpacing="-1">1×</text>
      <text x="160" y="158" textAnchor="middle" fontSize="11" fontFamily="var(--font-mono)" fill="#5b616e">GPU</text>
      <rect x="300" y="72" width="170" height="40" rx="20" fill="var(--color-wash)" stroke={L} />
      <text x="385" y="97" textAnchor="middle" fontSize="12" fontFamily="var(--font-mono)" fill="#5b616e">$ / TOKEN · API</text>
      <rect x="300" y="168" width="170" height="40" rx="20" fill={A} />
      <text x="385" y="193" textAnchor="middle" fontSize="12" fontFamily="var(--font-mono)" fill="#fff">$ / GPU-HOUR · LORDE</text>
      <path d="M246 140 H300 M300 92 V188" stroke={I} strokeWidth="1.5" fill="none" />
      <path d="M300 92 h0 M300 188 h0" stroke={I} />
    </Frame>
  );
}
