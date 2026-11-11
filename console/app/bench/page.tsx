import type { Metadata } from "next";

import { SiteFooter } from "@/components/site/footer";
import { SiteHeader } from "@/components/site/header";
import { DataTable, PageHero, Prose } from "@/components/site/page-hero";
import { Badge, Card, Container, LinkButton, SectionTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Lorde Bench", description: "What every Lorde release must prove before it serves." };

const METRICS = [
  ["Gate-usable rate", "Share of replies CuratorKIT's parser accepts", "The failure mode CuratorKIT warns about for small judges"],
  ["Decision agreement", "Judge and reference agree on pass/reject at 0.7", "The gate only acts on pass or reject"],
  ["Cohen's κ", "Agreement corrected for chance", "Pass rates are skewed; raw agreement flatters"],
  ["Spearman ρ, MAE", "Rank correlation and error of scores", "Calibration beyond the threshold"],
  ["Verdict accuracy", "Grounding verdict equals the reference", "Grounding is the higher-stakes gate"],
  ["Human agreement", "The same statistics against hand labels", "Keeps the teacher honest: its own agreement is the ceiling"],
  ["Downstream F1, groundedness", "AuditKIT scores of models trained on each gated set", "A judge is only as good as the data it lets through"],
  ["Latency, throughput, cost", "p50/p95, judgments per second, $ per 1M judgments", "Pipeline fit and the ROI"],
];

const CRITERIA: [string, string, string][] = [
  ["Gate-usable rate, unconstrained", "≥ 99.0%", "Yes"], ["Gate-usable rate, structured outputs", "100%", "Yes"],
  ["Decision agreement vs teacher", "≥ 90%", "Yes"], ["Cohen's κ vs teacher", "≥ 0.75", "Yes"],
  ["Spearman ρ / MAE vs teacher", "≥ 0.80 / ≤ 0.10", "Reported"], ["Grounding verdict accuracy", "≥ 85%", "Yes"],
  ["Human decision agreement", "≥ teacher's − 3 points", "Yes"], ["Per-language decision agreement", "≥ 85% in every language", "Yes"],
  ["Downstream F1 (Lorde vs teacher-gated)", "within 2% relative; above ungated, p < 0.05", "Yes (release)"],
  ["p95 latency, 16 concurrent, 1× L4", "≤ 1.5 s", "Yes (production)"],
  ["Adversarial-100 pass rate", "≤ 5%", "Yes"], ["No regression vs active revision", "agreement drop ≤ 1 point", "Yes"],
];

export default function Bench() {
  return (
    <>
      <SiteHeader />
      <main>
        <PageHero eyebrow="Lorde Bench" title="What every release must prove."
          lead="Lorde Bench is the gate between a trained revision and production. It runs in Forge, in CI against staging, and weekly in production to catch drift.">
          <LinkButton href="/console/reports" arrow>Active model report</LinkButton>
          <LinkButton href="/model" variant="secondary">Model card</LinkButton>
        </PageHero>

        <section className="py-20">
          <Container>
            <SectionTitle title="Metrics, and why each one." lead="Before/after on more than one metric, each with a reason. AuditKIT performs per-sample scoring and the significance tests." />
            <div className="mt-10">
              <DataTable head={["Metric", "Definition", "Why it matters"]} rows={METRICS.map(([a, b, c]) => [<span key="a" className="font-medium">{a}</span>, b, <span key="c" className="text-muted">{c}</span>])} />
            </div>
          </Container>
        </section>

        <section className="bg-wash py-20">
          <Container>
            <SectionTitle title="Acceptance criteria." lead="A revision becomes active only when every blocking criterion is met." />
            <div className="mt-10">
              <DataTable head={["Criterion", "Threshold", "Blocking"]}
                rows={CRITERIA.map(([c, t, b]) => [c, <span key="t" className="font-medium">{t}</span>,
                  <Badge key="b" tone={b.startsWith("Yes") ? "accent" : "neutral"}>{b}</Badge>])} className="bg-white" />
            </div>
          </Container>
        </section>

        <section className="py-20">
          <Container className="grid gap-4 md:grid-cols-3">
            {[["golden-300", "300 items (150 reward, 150 grounding) across all eight languages, with Aya Expanse 32B and human labels where available. Frozen per major version; used in CI."],
              ["adversarial-100", "Judged content that tries to instruct the judge, extreme lengths, mixed scripts and empty fields. Lorde must pass at most 5% of it."],
              ["drift-weekly", "200 production items sampled weekly, only from organizations that enabled retention, re-judged by Aya Expanse 32B."]].map(([t, d]) => (
              <Card key={t} className="p-6">
                <p className="font-mono text-[13px] text-accent">{t}</p>
                <p className="mt-3 text-[15px] leading-relaxed text-ink-2">{d}</p>
              </Card>
            ))}
          </Container>
          <Container className="mt-14">
            <Prose>
              <p className="max-w-3xl text-muted">Cost per 1M judgments is <code>GPU $/h ÷ (throughput × 3,600) × 10⁶</code> for Lorde and
                <code> tokens_in × price_in + tokens_out × price_out</code> per judgment × 10⁶ for an API judge. Results are
                published with each model revision; until then this page shows the bars, not the numbers.</p>
            </Prose>
          </Container>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
