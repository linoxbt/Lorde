"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui";

/** Errors above the Console screens (including the Console layout itself). */
export default function AppError({ error, retry }: { error: unknown; retry: () => void }) {
  useEffect(() => { console.error(error); }, [error]);
  const digest = error instanceof Error && "digest" in error ? String((error as { digest?: string }).digest ?? "") : "";
  return (
    <main className="mx-auto flex min-h-screen max-w-lg flex-col justify-center px-6">
      <h1 className="text-[28px] font-medium tracking-[-0.03em]">Something went wrong</h1>
      <p className="mt-2 text-[15px] text-muted">The page could not be loaded. Nothing was changed. Try again in a moment.</p>
      {digest && <p className="mt-3 font-mono text-[12px] text-faint">Reference {digest}</p>}
      <div className="mt-6 flex gap-2">
        <Button onClick={() => retry()}>Try again</Button>
        <a href="/" className="inline-flex h-10 items-center rounded-full px-4 text-sm text-muted hover:bg-wash hover:text-ink">Home</a>
      </div>
    </main>
  );
}
