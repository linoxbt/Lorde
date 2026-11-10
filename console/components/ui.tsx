import clsx from "clsx";
import { ChevronRight } from "lucide-react";
import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "inverse" | "inverse-soft" | "danger";
const variants: Record<Variant, string> = {
  primary: "bg-accent text-white hover:bg-accent-hover",
  secondary: "bg-white text-ink border border-line hover:border-line-2 hover:bg-wash",
  ghost: "text-ink hover:bg-wash",
  inverse: "bg-white text-accent-ink hover:bg-accent-wash",
  "inverse-soft": "bg-white/15 text-white hover:bg-white/25",
  danger: "bg-reject text-white hover:brightness-95",
};
const sizes = { sm: "h-8 px-3.5 text-[13px]", md: "h-10 px-5 text-sm", lg: "h-12 px-6 text-[15px]" };

export function buttonClass(variant: Variant = "primary", size: keyof typeof sizes = "md", extra?: string) {
  return clsx(
    "inline-flex items-center justify-center gap-2 rounded-full font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none whitespace-nowrap",
    variants[variant],
    sizes[size],
    extra,
  );
}

export function Button({ variant = "primary", size = "md", className, ...p }:
  ComponentProps<"button"> & { variant?: Variant; size?: keyof typeof sizes }) {
  return <button className={buttonClass(variant, size, className)} {...p} />;
}

export function LinkButton({ href, variant = "primary", size = "md", className, children, arrow }:
  { href: string; variant?: Variant; size?: keyof typeof sizes; className?: string; children: ReactNode; arrow?: boolean }) {
  return (
    <Link href={href} className={buttonClass(variant, size, className)}>
      {children}
      {arrow && <ChevronRight className="size-4 -mr-1" aria-hidden />}
    </Link>
  );
}

export function Container({ className, ...p }: ComponentProps<"div">) {
  return <div className={clsx("mx-auto w-full max-w-[1200px] px-5 sm:px-8", className)} {...p} />;
}

export function Eyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <p className={clsx("font-mono text-[11px] uppercase tracking-[0.14em] text-muted", className)}>{children}</p>
  );
}

export function Card({ className, ...p }: ComponentProps<"div">) {
  return <div className={clsx("rounded-[var(--radius-card)] border border-line bg-white", className)} {...p} />;
}

export function Badge({ tone = "neutral", children, className }:
  { tone?: "neutral" | "pass" | "reject" | "accent" | "warn"; children: ReactNode; className?: string }) {
  const t = {
    neutral: "bg-wash text-ink-2 border-line",
    pass: "bg-pass-wash text-[#006b4e] border-[#bfe6da]",
    reject: "bg-reject-wash text-[#9a4300] border-[#f6cfb3]",
    accent: "bg-accent-wash text-accent border-accent-soft",
    warn: "bg-[#fff6d9] text-[#7a5b00] border-[#f1df9c]",
  }[tone];
  return (
    <span className={clsx("inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[12px] font-medium", t, className)}>
      {children}
    </span>
  );
}

export function SectionTitle({ title, lead, action }: { title: ReactNode; lead?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
      <div className="max-w-2xl">
        <h2 className="text-[32px] leading-[1.1] font-medium tracking-[-0.035em] sm:text-[40px]">{title}</h2>
        {lead && <p className="mt-3 text-[17px] leading-relaxed text-muted">{lead}</p>}
      </div>
      {action}
    </div>
  );
}
