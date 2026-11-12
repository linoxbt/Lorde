import Link from "next/link";

/** Lorde's mark: concentric rings converging on a decision point (from the spec cover). */
export function Mark({ size = 28, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" className={className}>
      <circle cx="16" cy="16" r="15" fill="var(--color-accent)" />
      <circle cx="16" cy="16" r="10.5" fill="none" stroke="#fff" strokeOpacity="0.45" strokeWidth="1.5" />
      <circle cx="16" cy="16" r="6" fill="none" stroke="#fff" strokeOpacity="0.8" strokeWidth="1.5" />
      <circle cx="16" cy="16" r="2.25" fill="#fff" />
    </svg>
  );
}

export function Logo({ href = "/", label = true }: { href?: string; label?: boolean }) {
  return (
    <Link href={href} className="inline-flex items-center gap-2.5" aria-label="Lorde home">
      <Mark />
      {label && <span className="text-[19px] font-semibold tracking-[-0.03em]">Lorde</span>}
    </Link>
  );
}
