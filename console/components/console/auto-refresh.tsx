"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Re-render the server page every few seconds while `active` (for example while a job is queued or running). */
export function AutoRefresh({ active, every = 5000 }: { active: boolean; every?: number }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => { if (document.visibilityState === "visible") router.refresh(); }, every);
    return () => clearInterval(t);
  }, [active, every, router]);
  return null;
}
