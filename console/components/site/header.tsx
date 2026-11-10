import Link from "next/link";

import { Logo } from "@/components/brand";
import { HamburgerMenu } from "@/components/site/menu";
import { AnnouncementBar } from "@/components/site/announcement";
import { buttonClass } from "@/components/ui";
import { siteNav } from "@/lib/nav";

export function SiteHeader({ announcement = true }: { announcement?: boolean }) {
  return (
    <>
      {announcement && <AnnouncementBar />}
      <header className="sticky top-0 z-40 border-b border-transparent bg-white/85 backdrop-blur supports-[backdrop-filter]:bg-white/70">
        <div className="mx-auto flex h-[72px] max-w-[1200px] items-center justify-between gap-6 px-5 sm:px-8">
          <Logo />
          <nav aria-label="Main" className="hidden items-center gap-1 lg:flex">
            {[
              ["Product", "/docs#grounding"],
              ["Developers", "/docs"],
              ["Bench", "/bench"],
              ["Model", "/model"],
              ["Status", "/status"],
            ].map(([label, href]) => (
              <Link key={href} href={href!} className="rounded-full px-3.5 py-2 text-[15px] text-ink-2 hover:bg-wash hover:text-ink">
                {label}
              </Link>
            ))}
          </nav>
          <div className="flex items-center gap-2">
            <Link href="/console" className={buttonClass("primary", "sm", "hidden h-9 px-4 sm:inline-flex")}>
              Open console
            </Link>
            <HamburgerMenu groups={siteNav} />
          </div>
        </div>
      </header>
    </>
  );
}
