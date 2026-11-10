"use client";

import { ChevronRight, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

const KEY = "lorde.announcement.v1";

export function AnnouncementBar() {
  const [hidden, setHidden] = useState(false);
  useEffect(() => setHidden(localStorage.getItem(KEY) === "dismissed"), []);
  if (hidden) return null;
  return (
    <div className="relative bg-wash text-[13px] sm:text-sm">
      <div className="mx-auto flex max-w-[1200px] items-center justify-center gap-3 px-12 py-2.5 text-center">
        <span className="text-ink-2">
          Lorde v1 is our Track 1 submission to the Lexsi AI Safety &amp; Alignment Hackathon.
        </span>
        <span className="hidden h-3.5 w-px bg-line-2 sm:block" aria-hidden />
        <Link href="/model" className="hidden items-center font-medium text-accent hover:underline sm:inline-flex">
          Read the model card <ChevronRight className="size-3.5" aria-hidden />
        </Link>
      </div>
      <button
        type="button"
        aria-label="Dismiss announcement"
        onClick={() => { localStorage.setItem(KEY, "dismissed"); setHidden(true); }}
        className="absolute right-3 top-1/2 inline-flex size-7 -translate-y-1/2 items-center justify-center rounded-full text-muted hover:bg-wash-2 hover:text-ink"
      >
        <X className="size-4" aria-hidden />
      </button>
    </div>
  );
}
