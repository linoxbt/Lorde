"use client";

import { useEffect } from "react";

import { Button, Card } from "@/components/ui";

/** A Console screen failed to load (for example a database read): say so, keep the shell, offer a retry. */
export default function ConsoleError({ error, retry }: { error: unknown; retry: () => void }) {
  useEffect(() => { console.error(error); }, [error]);
  const digest = error instanceof Error && "digest" in error ? String((error as { digest?: string }).digest ?? "") : "";
  return (
    <Card className="p-8">
      <h1 className="text-[22px] font-medium tracking-[-0.02em]">This page could not be loaded</h1>
      <p className="mt-2 max-w-xl text-sm text-muted">
        Something went wrong while reading your workspace. Nothing was changed. Try again; if it keeps happening, check the
        status page.
      </p>
      {digest && <p className="mt-3 font-mono text-[12px] text-faint">Reference {digest}</p>}
      <div className="mt-5 flex gap-2">
        <Button onClick={() => retry()}>Try again</Button>
        <a href="/status" className="inline-flex h-10 items-center rounded-full px-4 text-sm text-muted hover:bg-wash hover:text-ink">Status</a>
      </div>
    </Card>
  );
}
