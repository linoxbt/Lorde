"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

type S = { state: "ok" | "degraded" | "down" | "demo" | "loading"; label: string };

export function StatusDot() {
  const [s, setS] = useState<S>({ state: "loading", label: "Checking status" });
  useEffect(() => {
    fetch("/api/status").then((r) => r.json()).then((d) => setS(d.summary)).catch(() =>
      setS({ state: "down", label: "Status unavailable" }));
  }, []);
  const color = { ok: "bg-pass", degraded: "bg-warn", down: "bg-reject", demo: "bg-accent", loading: "bg-faint" }[s.state];
  return (
    <Link href="/status" className="inline-flex items-center gap-2 text-sm font-medium text-accent hover:underline">
      <span className="relative flex size-2">
        {s.state === "ok" && <span className={`absolute inline-flex size-full animate-ping rounded-full ${color} opacity-60`} />}
        <span className={`relative inline-flex size-2 rounded-full ${color}`} />
      </span>
      {s.label}
    </Link>
  );
}
