import { ArrowUpRight, ChevronRight, FileCheck2, Layers, ScanSearch, Sparkles } from "lucide-react";
import Link from "next/link";

import { CodeTabs } from "@/components/site/code-tabs";
import { SiteFooter } from "@/components/site/footer";
import { SiteHeader } from "@/components/site/header";
import { HeroArt } from "@/components/site/hero-art";
import { ContractArt, CostArt, DownstreamArt, SchemaArt } from "@/components/site/illustrations";
import { Card, Container, Eyebrow, LinkButton, SectionTitle } from "@/components/ui";
import { env } from "@/lib/env";
import { snippets } from "@/lib/snippets";

const BUILT_WITH = ["CuratorKIT", "AlignTune", "AuditKIT", "Tiny Aya Global", "Aya Expanse", "Hugging Face", "vLLM",
  "Supabase", "Model Context Protocol"];

const GATES = [
  { icon: ScanSearch, title: "Grounding gate", href: "/docs#grounding",
    body: "Is every claim in an answer supported by its source? Lorde lists supported and unsupported claims, with a verdict." },
  { icon: Sparkles, title: "Reward gate", href: "/docs#reward",
    body: "Scores helpfulness, honesty and instruction following, with strengths and weaknesses CuratorKIT can act on." },
  { icon: Layers, title: "Gating jobs", href: "/docs#jobs",
    body: "Upload a JSONL, CSV or Parquet dataset. Get passed rows, rejected rows with reasons, and full provenance." },
];

const CRITERIA = [
  { value: "≥ 99%", label: "Gate-usable replies" },
  { value: "≥ 90%", label: "Agreement with teacher" },
  { value: "κ ≥ 0.75", label: "Chance-corrected" },
  { value: "≤ 1.5 s", label: "p95 at 16 concurrent" },
];

const PILLARS = [
  { art: ContractArt, title: "CuratorKIT's own contract",
    body: "Prompts are rendered and replies parsed by CuratorKIT itself, so a pass means exactly what CuratorKIT would accept." },
  { art: SchemaArt, title: "Never breaks the JSON",
    body: "Trained on canonical replies and served with structured outputs, so no good row is thrown away as unparseable." },
  { art: DownstreamArt, title: "Proves downstream value",
    body: "Data gated by Lorde must train models as well as data gated by its teacher, measured with AuditKIT." },
  { art: CostArt, title: "Cheap enough for every row",
    body: "A 3.35B model on one inexpensive GPU replaces a per-token API bill, so you can judge every row, not a sample." },
];

const STEPS = [
  { n: "01", tool: "CuratorKIT", title: "Curate", body: "aya_dataset prompts and Wikipedia QA, each shown good and degraded." },
  { n: "02", tool: "Aya Expanse", title: "Label", body: "The teacher answers CuratorKIT's exact gate prompts; CuratorKIT parses them." },
  { n: "03", tool: "AlignTune", title: "Train", body: "LoRA on Tiny Aya Global with canonical JSON targets, loss on the reply only." },
  { n: "04", tool: "AuditKIT", title: "Prove", body: "Agreement, human ceiling, downstream F1 and significance before any release." },
];

const LANGS = [["English", "en"], ["हिन्दी", "hi"], ["Français", "fr"], ["العربية", "ar"], ["Español", "es"],
  ["Português", "pt"], ["Bahasa Indonesia", "id"], ["Türkçe", "tr"]];

const NOTES = [
  { date: "Nov 9, 2026", title: "Lorde v1: the build specification", body: "From an empty repo to a judge you can run in production.", href: "/model", art: 0 },
  { date: "Nov 9, 2026", title: "Why these eight languages", body: "Every label comes from a teacher that actually reads the language.", href: "/model#languages", art: 1 },
  { date: "Nov 10, 2026", title: "What a release must prove", body: "Lorde Bench: gate-usable rate, κ, human ceiling and downstream F1.", href: "/bench", art: 2 },
  { date: "Nov 10, 2026", title: "Plugging Lorde into CuratorKIT", body: "One line in CuratorConfig, and every reply is gate-usable.", href: "/docs#curatorkit", art: 3 },
];

function NoteArt({ v }: { v: number }) {
  const bg = ["bg-accent", "bg-accent-ink", "bg-[#2b2a8c]", "bg-ink"][v];
  return (
    <div className={`relative h-40 overflow-hidden rounded-t-[var(--radius-card)] ${bg}`} aria-hidden>
      <div className="absolute inset-0 opacity-60"
        style={{
          backgroundImage: v % 2
            ? "radial-gradient(circle at 30% 50%, transparent 18px, rgba(255,255,255,.18) 19px, transparent 20px), radial-gradient(circle at 30% 50%, transparent 38px, rgba(255,255,255,.14) 39px, transparent 40px), radial-gradient(circle at 30% 50%, transparent 58px, rgba(255,255,255,.1) 59px, transparent 60px)"
            : "linear-gradient(90deg, rgba(255,255,255,.14) 1px, transparent 1px), linear-gradient(rgba(255,255,255,.14) 1px, transparent 1px)",
          backgroundSize: v % 2 ? "100% 100%" : "14px 14px",
        }} />
      <span className="absolute bottom-4 left-5 font-mono text-[11px] uppercase tracking-[0.18em] text-white/80">
        {["spec", "languages", "bench", "curatorkit"][v]}
      </span>
    </div>
  );
}

export default function Landing() {
  return (
    <>
      <SiteHeader />
      <main id="main" tabIndex={-1} className="outline-none">
        {/* Hero */}
        <section className="relative isolate overflow-hidden">
          <HeroArt />
          <Container className="relative flex min-h-[640px] flex-col items-center justify-center py-24 text-center sm:min-h-[700px]">
            <Eyebrow className="rounded-full border border-line bg-white/80 px-3 py-1 backdrop-blur">
              A 3B judge for CuratorKIT
            </Eyebrow>
            <h1 className="mt-6 max-w-4xl text-[52px] font-medium leading-[0.98] tracking-[-0.05em] sm:text-[80px] lg:text-[96px]">
              The judge for
              <br />
              trustworthy data.
            </h1>
            <p className="mt-6 max-w-xl text-[18px] leading-relaxed text-muted sm:text-[20px]">
              Lorde runs CuratorKIT&apos;s quality gates with API-grade decisions, on one inexpensive GPU, in eight languages.
            </p>
            <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
              <LinkButton href="/console" size="lg">
                <span className="inline-block size-3.5 rounded-full border-2 border-white/90" aria-hidden />
                Start gating
              </LinkButton>
              <LinkButton href="/docs" size="lg" variant="secondary">Read the docs</LinkButton>
            </div>
          </Container>
        </section>

        {/* Built with */}
        <section aria-label="Built with" className="border-y border-line py-8">
          <p className="text-center text-sm text-muted">Built by chaining open toolkits and open weights.</p>
          <div className="relative mt-5 overflow-hidden [mask-image:linear-gradient(90deg,transparent,#000_12%,#000_88%,transparent)]">
            <div className="flex w-max animate-marquee gap-14 pr-14">
              {[...BUILT_WITH, ...BUILT_WITH].map((n, i) => (
                <span key={i} className="text-[22px] font-semibold tracking-[-0.03em] text-[#9aa0ab]">{n}</span>
              ))}
            </div>
          </div>
        </section>

        {/* Gates */}
        <section className="py-20">
          <Container className="grid gap-4 md:grid-cols-3">
            {GATES.map(({ icon: Icon, title, body, href }) => (
              <Link key={title} href={href} className="group">
                <Card className="h-full p-6 transition-colors group-hover:border-line-2 group-hover:bg-wash/60">
                  <div className="flex items-start justify-between">
                    <span className="inline-flex size-10 items-center justify-center rounded-xl bg-accent-wash text-accent">
                      <Icon className="size-5" aria-hidden />
                    </span>
                    <ChevronRight className="size-4 text-faint transition-transform group-hover:translate-x-0.5" aria-hidden />
                  </div>
                  <h3 className="mt-6 text-[18px] font-medium tracking-[-0.02em]">{title}</h3>
                  <p className="mt-2 text-[15px] leading-relaxed text-muted">{body}</p>
                </Card>
              </Link>
            ))}
          </Container>
        </section>

        {/* Release criteria band */}
        <section className="bg-wash py-24">
          <Container className="text-center">
            <h2 className="text-[32px] font-medium tracking-[-0.035em] sm:text-[40px]">Where every row earns its place.</h2>
            <p className="mx-auto mt-3 max-w-xl text-[17px] text-muted">
              No Lorde release serves a request until it clears Lorde Bench. These are the bars it must clear.
            </p>
            <dl className="mx-auto mt-14 grid max-w-4xl grid-cols-2 gap-y-10 md:grid-cols-4 md:divide-x md:divide-line-2">
              {CRITERIA.map((c) => (
                <div key={c.label} className="px-4">
                  <dt className="sr-only">{c.label}</dt>
                  <dd className="tnum text-[34px] font-medium tracking-[-0.04em]">{c.value}</dd>
                  <dd className="mt-1 font-mono text-[11px] uppercase tracking-[0.14em] text-muted">{c.label}</dd>
                </div>
              ))}
            </dl>
            <Link href="/bench" className="mt-12 inline-flex items-center gap-1 text-sm font-medium text-accent hover:underline">
              How Lorde Bench measures them <ChevronRight className="size-4" aria-hidden />
            </Link>
          </Container>
        </section>

        {/* Pillars */}
        <section className="py-24">
          <Container>
            <SectionTitle
              title={<><span className="text-ink">Lorde</span> <span className="text-faint">is the judge for data you can actually train on.</span></>}
              action={<LinkButton href="/bench" variant="secondary" arrow>Explore Lorde Bench</LinkButton>}
            />
            <div className="mt-14 grid gap-x-6 gap-y-14 md:grid-cols-2">
              {PILLARS.map(({ art: Art, title, body }) => (
                <article key={title}>
                  <Card className="aspect-[13/7] overflow-hidden p-6"><Art /></Card>
                  <h3 className="mt-5 text-[19px] font-medium tracking-[-0.02em]">{title}</h3>
                  <p className="mt-2 max-w-lg text-[15px] leading-relaxed text-muted">{body}</p>
                </article>
              ))}
            </div>
          </Container>
        </section>

        {/* How it works */}
        <section className="border-t border-line py-24">
          <Container>
            <SectionTitle title="Three Lexsi toolkits, one pipeline."
              lead="Lorde Forge chains CuratorKIT, AlignTune and AuditKIT into a pipeline that runs on a free Kaggle notebook." />
            <ol className="mt-14 grid gap-4 md:grid-cols-4">
              {STEPS.map((s) => (
                <li key={s.n}>
                  <Card className="h-full p-6">
                    <div className="flex items-center justify-between">
                      <span className="tnum font-mono text-[12px] text-faint">{s.n}</span>
                      <span className="rounded-full bg-wash px-2.5 py-1 font-mono text-[11px] text-ink-2">{s.tool}</span>
                    </div>
                    <h3 className="mt-8 text-[22px] font-medium tracking-[-0.03em]">{s.title}</h3>
                    <p className="mt-2 text-[14px] leading-relaxed text-muted">{s.body}</p>
                  </Card>
                </li>
              ))}
            </ol>
          </Container>
        </section>

        {/* Developers */}
        <section className="py-24">
          <Container className="grid items-start gap-12 lg:grid-cols-[1fr_1.25fr]">
            <div>
              <Eyebrow>For developers</Eyebrow>
              <h2 className="mt-4 text-[32px] font-medium leading-[1.1] tracking-[-0.035em] sm:text-[40px]">
                Wherever your data lives, Lorde is one call away.
              </h2>
              <p className="mt-4 text-[17px] leading-relaxed text-muted">
                An OpenAI-compatible endpoint CuratorKIT uses with no code change, native judge endpoints, a typed Python
                SDK, an MCP server for coding agents, and gating jobs for whole datasets.
              </p>
              <ul className="mt-8 space-y-3 text-[15px]">
                {["Errors with codes and request ids, never silent", "Keys scoped per organization, revocable in seconds",
                  "No judged content stored by default"].map((t) => (
                  <li key={t} className="flex gap-3"><FileCheck2 className="mt-0.5 size-[18px] shrink-0 text-accent" aria-hidden />{t}</li>
                ))}
              </ul>
              <div className="mt-9 flex flex-wrap gap-3">
                <LinkButton href="/docs" arrow>Quickstart</LinkButton>
                <LinkButton href="/console/keys" variant="secondary">Get an API key</LinkButton>
              </div>
            </div>
            <CodeTabs snippets={snippets(env.publicApiUrl)} />
          </Container>
        </section>

        {/* Languages */}
        <section className="border-y border-line bg-wash py-16">
          <Container>
            <div className="flex flex-col gap-8 lg:flex-row lg:items-center lg:justify-between">
              <div className="max-w-md">
                <h2 className="text-[26px] font-medium tracking-[-0.03em]">Eight languages. One teacher that reads them all.</h2>
                <p className="mt-2 text-[15px] text-muted">Every language is supported by both the teacher and the student.</p>
              </div>
              <ul className="flex flex-wrap gap-2 lg:max-w-[620px] lg:justify-end">
                {LANGS.map(([name, code]) => (
                  <li key={code} lang={code} dir={code === "ar" ? "rtl" : undefined}
                    className="rounded-full border border-line bg-white px-4 py-2 text-[15px]">{name}</li>
                ))}
              </ul>
            </div>
          </Container>
        </section>

        {/* Latest */}
        <section className="py-24">
          <Container>
            <div className="flex items-center justify-between">
              <h2 className="text-[28px] font-medium tracking-[-0.03em]">Latest on Lorde</h2>
              <LinkButton href="/model" variant="secondary" size="sm" arrow>View all</LinkButton>
            </div>
            <div className="-mx-5 mt-8 flex snap-x gap-4 overflow-x-auto px-5 pb-2 sm:mx-0 sm:grid sm:grid-cols-2 sm:overflow-visible sm:px-0 lg:grid-cols-4">
              {NOTES.map((n) => (
                <Link key={n.title} href={n.href} className="group w-[280px] shrink-0 snap-start sm:w-auto">
                  <article className="flex h-full flex-col rounded-[var(--radius-card)] bg-wash transition-colors group-hover:bg-wash-2">
                    <NoteArt v={n.art} />
                    <div className="flex flex-1 flex-col p-5">
                      <h3 className="font-medium tracking-[-0.01em]">{n.title}</h3>
                      <p className="mt-1.5 text-sm text-muted">{n.body}</p>
                      <p className="mt-auto pt-6 text-[13px] text-faint">{n.date}</p>
                    </div>
                  </article>
                </Link>
              ))}
            </div>
          </Container>
        </section>

        {/* CTA band */}
        <section className="bg-accent text-white">
          <Container className="flex flex-col gap-10 py-20 md:flex-row md:items-end md:justify-between">
            <div className="max-w-xl">
              <h2 className="text-[34px] font-medium leading-[1.05] tracking-[-0.04em] sm:text-[44px]">Start gating with Lorde.</h2>
              <p className="mt-3 text-[17px] text-white/75">Open weights, an open contract, and an honest record of every decision.</p>
            </div>
            <div className="flex flex-wrap gap-3">
              <LinkButton href="/console" variant="inverse" size="lg">Open console</LinkButton>
              <Link href="/docs" className="inline-flex h-12 items-center gap-2 rounded-full bg-white/15 px-6 text-[15px] font-medium hover:bg-white/25">
                Read the docs <ArrowUpRight className="size-4" aria-hidden />
              </Link>
            </div>
          </Container>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
