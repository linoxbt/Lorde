import Link from "next/link";

import { Mark } from "@/components/brand";
import { StatusDot } from "@/components/site/status-dot";
import { Container } from "@/components/ui";
import { siteNav } from "@/lib/nav";

export function SiteFooter() {
  return (
    <footer className="border-t border-line bg-white">
      <Container className="grid gap-12 py-16 md:grid-cols-[1.2fr_repeat(4,1fr)]">
        <div>
          <StatusDot />
          <p className="mt-3 text-[15px] text-ink-2">Judge the data. Trust the model.</p>
          <div className="mt-5 flex items-center gap-3 text-muted">
            <a href="https://github.com/linoxbt/Lorde" className="text-sm hover:text-ink">GitHub</a>
            <span aria-hidden>·</span>
            <a href="https://huggingface.co" className="text-sm hover:text-ink">Hugging Face</a>
          </div>
        </div>
        {siteNav.map((g) => (
          <div key={g.label}>
            <p className="text-sm font-medium">{g.label}</p>
            <ul className="mt-3 space-y-2.5">
              {g.links.map((l) => (
                <li key={l.label}>
                  <Link href={l.href} className="text-sm text-muted hover:text-ink">{l.label}</Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </Container>
      <Container aria-hidden className="select-none overflow-hidden">
        <div className="flex items-end gap-4 pb-2">
          <Mark size={120} className="hidden shrink-0 opacity-[0.07] grayscale sm:block" />
          <p className="translate-y-[18%] text-[clamp(96px,22vw,300px)] font-semibold leading-none tracking-[-0.06em] text-wash-2">
            lorde
          </p>
        </div>
      </Container>
      <Container className="flex flex-col gap-3 border-t border-line py-6 text-[13px] text-muted sm:flex-row sm:items-center sm:justify-between">
        <p>© 2026 Lorde · Model weights CC-BY-NC-4.0 · Built on Tiny Aya Global by Cohere Labs</p>
        <div className="flex gap-4">
          <Link href="/model#licence" className="hover:text-ink">Licences</Link>
          <Link href="/status" className="hover:text-ink">Status</Link>
          <Link href="/docs" className="hover:text-ink">Docs</Link>
        </div>
      </Container>
    </footer>
  );
}
