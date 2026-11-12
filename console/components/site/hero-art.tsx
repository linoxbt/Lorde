/** Animated concentric rings with drifting judgment chips behind the hero headline. */
const CHIPS = [
  { t: "grounded · 0.94", x: "8%", y: "18%", tone: "pass", d: "0s" },
  { t: "reject · 0.18", x: "80%", y: "14%", tone: "reject", d: "1.2s" },
  { t: "helpfulness 0.88", x: "86%", y: "58%", tone: "accent", d: "2.1s" },
  { t: "pass ≥ 0.70", x: "4%", y: "64%", tone: "accent", d: "0.6s" },
  { t: "hallucinated", x: "16%", y: "86%", tone: "reject", d: "1.7s" },
  { t: "gate-usable ✓", x: "74%", y: "86%", tone: "pass", d: "2.6s" },
] as const;

const TONE = {
  pass: "border-[#bfe6da] bg-pass-wash text-[#006b4e]",
  reject: "border-[#f6cfb3] bg-reject-wash text-[#9a4300]",
  accent: "border-accent-soft bg-accent-wash text-accent",
};

export function HeroArt() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="absolute left-1/2 top-[46%] size-[900px] -translate-x-1/2 -translate-y-1/2">
        {[0, 1.5, 3, 4.5].map((d) => (
          <span key={d} className="absolute inset-0 animate-ring rounded-full border border-accent/30"
            style={{ animationDelay: `${d}s` }} />
        ))}
        <span className="absolute inset-[38%] rounded-full border border-line" />
        <span className="absolute inset-[30%] rounded-full border border-dashed border-line" />
      </div>
      <div className="hidden md:block">
        {CHIPS.map((c) => (
          <span key={c.t}
            className={`absolute animate-float rounded-full border px-3 py-1 font-mono text-[11px] shadow-[0_1px_0_rgba(0,0,0,0.03)] ${TONE[c.tone]}`}
            style={{ left: c.x, top: c.y, animationDelay: c.d }}>
            {c.t}
          </span>
        ))}
      </div>
    </div>
  );
}
