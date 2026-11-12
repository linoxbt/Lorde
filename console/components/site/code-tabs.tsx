"use client";

import clsx from "clsx";
import { Check, Copy } from "lucide-react";
import { useState } from "react";

export type Snippet = { id: string; label: string; lang: string; code: string };

export function CodeTabs({ snippets }: { snippets: Snippet[] }) {
  const [active, setActive] = useState(snippets[0]?.id);
  const [copied, setCopied] = useState(false);
  const s = snippets.find((x) => x.id === active) ?? snippets[0]!;
  return (
    <div className="overflow-hidden rounded-[var(--radius-card)] border border-[#23252b] bg-[#0d0e12] text-[#e6e8ec]">
      <div className="flex items-center justify-between border-b border-white/10 px-2">
        <div role="tablist" aria-label="Code examples" className="flex overflow-x-auto">
          {snippets.map((x) => (
            <button key={x.id} role="tab" aria-selected={x.id === s.id} onClick={() => setActive(x.id)}
              className={clsx("relative px-4 py-3 text-[13px] whitespace-nowrap transition-colors",
                x.id === s.id ? "text-white" : "text-white/55 hover:text-white/80")}>
              {x.label}
              {x.id === s.id && <span className="absolute inset-x-3 bottom-0 h-0.5 rounded bg-accent-soft" />}
            </button>
          ))}
        </div>
        <button type="button" aria-label="Copy code"
          onClick={() => { navigator.clipboard?.writeText(s.code); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
          className="mr-2 inline-flex size-8 items-center justify-center rounded-full text-white/60 hover:bg-white/10 hover:text-white">
          {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
        </button>
      </div>
      <pre role="tabpanel" className="overflow-x-auto p-5 font-mono text-[13px] leading-6"><code>{s.code}</code></pre>
    </div>
  );
}
