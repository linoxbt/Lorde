import type { Metadata } from "next";

import { SiteFooter } from "@/components/site/footer";
import { SiteHeader } from "@/components/site/header";
import { DataTable, PageHero, Prose } from "@/components/site/page-hero";
import { Card, Container, LinkButton, SectionTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Model card", description: "Lorde: base, data, method, intended use, limits and licences." };

const SPEC: [string, string][] = [
  ["Model id", "<hf-user>/lorde-tiny-aya (revision = release tag, e.g. v1.0.0)"],
  ["Base", "CohereLabs/tiny-aya-global · 3.35B parameters · CC-BY-NC-4.0 + Cohere Labs Acceptable Use Policy"],
  ["Teacher", "CohereLabs/aya-expanse-8b (self-hosted, bulk labels); c4ai-aya-expanse-32b via the Cohere API (calibration)"],
  ["Method", "Supervised distillation: LoRA SFT on teacher replies re-serialized to canonical JSON, merged into the base"],
  ["LoRA", "r = 16, α = 32, dropout 0.05; q, k, v, o, gate, up and down projections"],
  ["Optimization", "lr 1e-4, cosine, 3% warmup, 2 epochs, effective batch 16, max 2,048 tokens (longer rows dropped)"],
  ["Decoding", "temperature 0.1, max_tokens 512: CuratorKIT's judge defaults"],
  ["Output", "Short canonical JSON with exactly the keys the gate reads"],
];

const LANGS = [["English", "en"], ["Hindi", "hi"], ["French", "fr"], ["Arabic", "ar"], ["Spanish", "es"], ["Portuguese", "pt"],
  ["Indonesian", "id"], ["Turkish", "tr"]];

const LICENCES: [string, string, string][] = [
  ["Tiny Aya Global (base)", "CC-BY-NC-4.0 + Acceptable Use Policy", "Lorde's weights are non-commercial"],
  ["Aya Expanse 8B / 32B", "CC-BY-NC-4.0", "Teacher outputs used for research training"],
  ["CuratorKIT", "Lexsi Labs LSAL v1.1", "Non-commercial; paid services need a Lexsi licence"],
  ["AuditKIT", "Lexsi Labs LSAL v1.2", "Free for research; acknowledge use"],
  ["AlignTune", "MIT", "No restriction beyond notice"],
];

export default function Model() {
  return (
    <>
      <SiteHeader announcement={false} />
      <main>
        <PageHero eyebrow="Model card" title="Lorde, Tiny Aya Global trained to judge."
          lead="A 3.35B judge for CuratorKIT's RewardGate and HallucinationGate, distilled from an Aya Expanse teacher on CuratorKIT's exact prompts.">
          <LinkButton href="/bench" arrow>Lorde Bench</LinkButton>
          <LinkButton href="https://github.com/linoxbt/Lorde" variant="secondary">Source on GitHub</LinkButton>
        </PageHero>
        <section className="py-20">
          <Container>
            <SectionTitle title="Specification." />
            <div className="mt-10"><DataTable head={["Property", "Value"]} rows={SPEC.map(([k, v]) => [<span key="k" className="font-medium">{k}</span>, v])} /></div>
          </Container>
        </section>
        <section className="bg-wash py-20">
          <Container className="grid gap-12 lg:grid-cols-2">
            <div>
              <h2 className="text-[28px] font-medium tracking-[-0.03em]">Training data</h2>
              <Prose>
                <p className="mt-4">Reward items: human-written prompts and answers from <code>CohereLabs/aya_dataset</code>, 300 per
                  language. Grounding items: Wikipedia passages with QA pairs from CuratorKIT&apos;s QA stage. Every prompt appears
                  good and degraded: the student&apos;s own answer, truncation, off-topic and wrong-language answers, padding; swapped
                  answers, fabricated numbers and closed-book answers.</p>
                <p>Splits are grouped by prompt and source passage, so no test prompt appears in training. The downstream sets come
                  from Wikipedia articles never used for judge training.</p>
              </Prose>
            </div>
            <div id="languages" className="scroll-mt-28">
              <h2 className="text-[28px] font-medium tracking-[-0.03em]">Languages</h2>
              <p className="mt-4 text-[16px] leading-relaxed text-ink-2">Eight languages, each supported by both the teacher (Aya
                Expanse covers 23) and the student, so every label comes from a teacher that actually reads the language.</p>
              <ul className="mt-6 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {LANGS.map(([n, c]) => (
                  <li key={c} className="rounded-xl border border-line bg-white px-3 py-2.5 text-sm">
                    <span className="font-mono text-[11px] uppercase text-faint">{c}</span><br />{n}
                  </li>
                ))}
              </ul>
            </div>
          </Container>
        </section>
        <section className="py-20">
          <Container className="grid gap-4 md:grid-cols-2">
            <Card className="p-6">
              <h3 className="text-[18px] font-medium">Intended use</h3>
              <p className="mt-2 text-[15px] leading-relaxed text-muted">Judging CuratorKIT RewardGate and HallucinationGate prompts in the
                eight training languages, to curate training data.</p>
            </Card>
            <Card className="p-6">
              <h3 className="text-[18px] font-medium">Out of scope</h3>
              <p className="mt-2 text-[15px] leading-relaxed text-muted">Other rubrics or prompt templates (flagged by template fingerprint),
                final decisions about people, and content moderation. Lorde inherits its teacher&apos;s blind spots; keep a human in
                the loop for high-stakes datasets.</p>
            </Card>
          </Container>
        </section>
        <section id="licence" className="scroll-mt-28 border-t border-line py-20">
          <Container>
            <SectionTitle title="Licences." lead="Lorde is for research and non-commercial use. Charging for it as a service needs commercial licences from Cohere and Lexsi Labs first." />
            <div className="mt-10"><DataTable head={["Component", "Licence", "What it means"]} rows={LICENCES.map(([a, b, c]) => [<span key="a" className="font-medium">{a}</span>, b, <span key="c" className="text-muted">{c}</span>])} /></div>
          </Container>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
