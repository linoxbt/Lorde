import type { Metadata } from "next";
import Link from "next/link";

import { CodeTabs } from "@/components/site/code-tabs";
import { SiteFooter } from "@/components/site/footer";
import { SiteHeader } from "@/components/site/header";
import { DataTable, PageHero, Prose } from "@/components/site/page-hero";
import { Card, Container, LinkButton } from "@/components/ui";
import { env } from "@/lib/env";
import { snippets } from "@/lib/snippets";

export const metadata: Metadata = { title: "Docs", description: "Quickstart for the Lorde API, SDK, MCP server and CuratorKIT preset." };

const TOC = [["quickstart", "Quickstart"], ["grounding", "Grounding gate"], ["reward", "Reward gate"], ["curatorkit", "CuratorKIT"],
  ["jobs", "Gating jobs"], ["sdk", "Python SDK"], ["mcp", "MCP server"], ["api", "API reference"], ["errors", "Errors and limits"]];

const API = [
  ["POST", "/v1/judge/reward", "Judge one instruction/response pair", "fields ≤ 32,000 chars"],
  ["POST", "/v1/judge/grounding", "Judge one source/question/answer item", "source ≤ 24,000 chars"],
  ["POST", "/v1/judge/batch", "Up to 256 items of one kind", "body ≤ 5 MB"],
  ["POST", "/v1/chat/completions", "OpenAI-compatible, for CuratorKIT and LiteLLM", "temperature ≤ 0.3"],
  ["GET", "/v1/models", "The served model and its revision", "–"],
  ["POST", "/v1/uploads", "Signed upload URL for a dataset", "≤ 200 MB"],
  ["POST", "/v1/jobs", "Create a gating job", "≤ 3 active per org"],
  ["GET", "/v1/jobs/{id}", "Status, progress and counts", "–"],
  ["GET", "/v1/jobs/{id}/results", "Signed URLs for results and provenance", "15-minute URLs"],
  ["POST", "/v1/jobs/{id}/cancel", "Cancel a queued or running job", "–"],
  ["GET", "/v1/usage", "Usage by day, key and endpoint", "scope usage:read"],
  ["*", "/mcp", "MCP Streamable HTTP endpoint", "same key"],
];

const ERRORS = [
  ["400", "invalid_request", "Schema validation failed"], ["401", "unauthenticated", "Missing or unknown key"],
  ["403", "forbidden", "Key revoked or lacks the scope"], ["404", "not_found", "Job or upload not in this org"],
  ["409", "conflict", "Job already finished or cancelled"], ["413", "payload_too_large", "Field, batch or upload over the limit"],
  ["422", "judge_unparseable", "The reply failed the contract (≈ 0 with structured outputs)"],
  ["429", "rate_limited", "Token bucket empty; Retry-After is set"], ["503", "model_unavailable", "Serve unhealthy; retry with backoff"],
];

function H2({ id, children }: { id: string; children: React.ReactNode }) {
  return <h2 id={id} className="scroll-mt-28 pt-4 text-[28px] font-medium tracking-[-0.03em]">{children}</h2>;
}

export default function Docs() {
  return (
    <>
      <SiteHeader />
      <main>
        <PageHero eyebrow="Docs" title="Judge your first item in five minutes."
          lead="Lorde speaks OpenAI-compatible chat, a native judge API, Python, and MCP. Pick the surface you already use.">
          <LinkButton href="/console/keys" arrow>Get an API key</LinkButton>
          <LinkButton href="/console/playground" variant="secondary">Try the playground</LinkButton>
        </PageHero>
        <Container className="grid gap-12 py-16 lg:grid-cols-[220px_1fr]">
          <nav aria-label="On this page" className="hidden lg:block">
            <ul className="sticky top-28 space-y-1 text-sm">
              {TOC.map(([id, label]) => (
                <li key={id}><a href={`#${id}`} className="block rounded-lg px-3 py-1.5 text-muted hover:bg-wash hover:text-ink">{label}</a></li>
              ))}
            </ul>
          </nav>
          <article className="min-w-0 space-y-12">
            <section className="space-y-5">
              <H2 id="quickstart">Quickstart</H2>
              <Prose>
                <p>Create a key in the Console (keys start with <code>lorde_sk_</code> and are shown once), then set
                  <code>LORDE_API_KEY</code> and <code>LORDE_BASE_URL</code>. Every example below uses them.</p>
              </Prose>
              <CodeTabs snippets={snippets(env.publicApiUrl)} />
            </section>
            <section className="space-y-4">
              <H2 id="grounding">Grounding gate</H2>
              <Prose>
                <p>CuratorKIT&apos;s HallucinationGate asks: is every claim in the answer supported by the source text? Claims from
                  outside knowledge count as unsupported even when true. Lorde returns <code>grounding_score</code> (0 to 1),
                  supported and unsupported claims, a <code>verdict</code> (grounded, partially_grounded or hallucinated) and a
                  <code>decision</code>: pass when the score is at or above the threshold, 0.7 by default.</p>
              </Prose>
            </section>
            <section className="space-y-4">
              <H2 id="reward">Reward gate</H2>
              <Prose>
                <p>CuratorKIT&apos;s RewardGate rates a response for helpfulness, honesty and instruction following. Lorde returns
                  <code>overall_score</code>, the three dimension scores, brief strengths and weaknesses, and the decision.</p>
              </Prose>
            </section>
            <section className="space-y-4">
              <H2 id="curatorkit">CuratorKIT</H2>
              <Prose>
                <p>Point CuratorKIT&apos;s <code>judge_llm</code> at the Gateway with <code>lorde.curatorkit.judge_llm()</code>. The
                  Gateway recognises CuratorKIT&apos;s gate prompts by their template fingerprint and constrains each reply to the
                  gate&apos;s JSON schema, so no row is ever rejected as unparseable. Prompts with an unknown fingerprint are still
                  served and marked <code>x-lorde-template: unknown</code>.</p>
              </Prose>
            </section>
            <section className="space-y-4">
              <H2 id="jobs">Gating jobs</H2>
              <Prose>
                <p>For whole datasets, upload a JSONL, CSV or Parquet file (up to 200 MB) and create a job. A worker runs CuratorKIT
                  exactly as you would, with Lorde as the judge, and returns <code>passed.jsonl</code>, <code>rejected.jsonl</code>
                  with reasons, <code>report.md</code>, the CuratorKIT manifest and <code>lexsi_provenance.json</code>. Each
                  organization can have three jobs queued or running at once.</p>
              </Prose>
              <div className="grid gap-3 sm:grid-cols-3">
                {[["1. Upload", "POST /v1/uploads returns a signed URL; PUT the file to it."],
                  ["2. Gate", "POST /v1/jobs with the upload id and thresholds."],
                  ["3. Download", "GET /v1/jobs/{id}/results returns 15-minute signed URLs."]].map(([t, d]) => (
                  <Card key={t} className="p-5"><p className="font-medium">{t}</p><p className="mt-1.5 text-sm text-muted">{d}</p></Card>
                ))}
              </div>
            </section>
            <section className="space-y-4">
              <H2 id="sdk">Python SDK</H2>
              <Prose>
                <p><code>pip install lorde</code> gives typed sync and async clients, retries with exponential backoff that honour
                  <code>Retry-After</code>, the CuratorKIT preset, the <code>lorde</code> CLI and a stdio MCP server. The SDK refuses
                  to talk to a Gateway with an incompatible major API version.</p>
              </Prose>
            </section>
            <section className="space-y-4">
              <H2 id="mcp">MCP server</H2>
              <Prose>
                <p>Tools: <code>lorde_judge_grounding</code>, <code>lorde_judge_reward</code>, <code>lorde_judge_batch</code> (up to
                  64 items), <code>lorde_create_gating_job</code>, <code>lorde_job_status</code> and <code>lorde_model_info</code>.
                  Every call is authenticated with your key and metered like an API call.</p>
              </Prose>
            </section>
            <section className="space-y-4">
              <H2 id="api">API reference</H2>
              <Prose><p>Base URL <code>{env.publicApiUrl}</code>. JSON over HTTPS with <code>Authorization: Bearer lorde_sk_…</code>.
                Every response carries <code>x-request-id</code>; judge responses add <code>x-lorde-model</code> and
                <code>x-lorde-template</code>.</p></Prose>
              <DataTable head={["Method", "Path", "Purpose", "Limit"]}
                rows={API.map(([m, p, d, l]) => [<span key="m" className="font-mono text-[12px]">{m}</span>,
                  <code key="p" className="font-mono text-[13px]">{p}</code>, d, <span key="l" className="text-muted">{l}</span>])} />
            </section>
            <section className="space-y-4">
              <H2 id="errors">Errors and limits</H2>
              <Prose><p>Errors look like <code>{`{"error": {"code", "message", "request_id"}}`}</code>. Defaults: 600 judgments per
                minute per key and 3 active jobs per organization, adjustable per key.</p></Prose>
              <DataTable head={["HTTP", "Code", "When"]}
                rows={ERRORS.map(([h, c, w]) => [h, <code key="c" className="font-mono text-[13px]">{c}</code>, w])} />
              <p className="text-sm text-muted">Next: <Link href="/bench" className="text-accent hover:underline">how every release is measured</Link>.</p>
            </section>
          </article>
        </Container>
      </main>
      <SiteFooter />
    </>
  );
}
