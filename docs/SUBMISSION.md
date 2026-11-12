# Hackathon submission checklist

Deadline: **14 Nov 2026, end of day AoE**. Your latest submission counts, so submit early and resubmit.
Shortlist announced 16 Nov; Demo Day 22 Nov (shortlisted teams get $200 in HF credits for 16-21 Nov).

## Rules (hackathon.lexsi.ai) → where Lorde meets them

| Rule | Lorde | Status |
|---|---|---|
| Track 1: chain ≥ 2 repos, 3+ preferred | CuratorKIT → AlignTune → AuditKIT (all three) | ☐ |
| Cohere models only (core) | Tiny Aya Global (student), Aya Expanse 8B (teacher); Aya Expanse 32B only as the (planned, not run) calibration and the ROI price reference | ☐ |
| All artifacts on Hugging Face | model, adapters/merged weights, data, labels, logs, results | ☐ |
| Deployed publicly | Live Console https://lorde-console.vercel.app (Gateway on Railway) + public GitHub repo. A demo Space needs HF PRO, so it is optional. | ☐ |
| Before/after on more than one metric, with rationale | `results/judge_eval.md` + article "Metrics, and why" | ☐ |
| Write-up: metrics, approach, datasets/tasks/models rationale, why better | `docs/HF_ARTICLE.md` (HF Article) | ☐ |
| ROI vs closed model (optional, preferred) | article ROI table: volume, GPU hours, $/h, tokens/s, API cost | ☐ |

## Submission form needs

- [ ] Registered email
- [ ] Abstract under 500 words (draft below)
- [ ] **HF model collection** "Lorde models": every repo **public and un-gated** (`09_publish.py --public`)
- [ ] **HF dataset collection** "Lorde datasets": public, un-gated
- [ ] Write-up: HF Article (or this repo's README if Articles are unavailable)
- [ ] GitHub repo with reproducibility notes (README) and the Kaggle notebook
- [ ] Repos and models used, track, problem statements:
  - Repos: CuratorKIT, AlignTune, AuditKIT
  - Models: Tiny Aya Global, Tiny Aya Base (downstream), Aya Expanse 8B (Aya Expanse 32B: price reference only)
  - Track 1 · problem statements: *Replacing a closed-model API*, *Data-scarce domains*
- [ ] Keep the Kaggle notebook and `results/` for the thesis week (16-21 Nov)

## Abstract (numbers from the v1 result files; keep in step with `docs/HF_ARTICLE.md`)

> **Lorde: a 3B judge that runs CuratorKIT's quality gates without breaking their JSON contract.** CuratorKIT
> curates training data with two LLM-judge gates, RewardGate and HallucinationGate. It parses the judge's JSON
> strictly and rejects a sample whenever the reply cannot be parsed, so small local judges, which often break
> the JSON, throw data away. We fine-tuned Tiny Aya Global (3.35B) to imitate an Aya Expanse 8B teacher's replies
> to CuratorKIT's exact gate prompts, using CuratorKIT's own prompt builders and parser as the contract. The pool
> holds 6,720 reward and grounding items in eight languages (aya_dataset prompts and Wikipedia QA from
> CuratorKIT's QA stage), each shown good and degraded; we trained a LoRA with AlignTune on canonical JSON
> targets. On a 937-item held-out test set grouped by prompt, Lorde's replies are 100.0% gate-usable (base:
> 74.2%) and agree with the teacher on 91.1% of pass/reject decisions (κ = 0.748; base: 64.5%); on the balanced
> golden-300 subset, 87.3% (κ = 0.691). That measures imitation: the teacher is lenient on grounding (it passes
> 70% of swapped answers and 94% of fabricated numbers), the 200 hand labels are not collected yet and the 32B
> calibration was not run, and Lorde misses its own promotion criteria (κ, Arabic at 84.5%, golden-300,
> adversarial-100 at 46%). In real CuratorKIT runs on 3,600 items, Lorde's judge-error rate is 0.4% against
> 97.6% for the base model and 11.4% for the teacher. Models trained on Lorde-gated data reach F1 0.681
> (teacher-gated 0.679, ungated 0.664; AuditKIT paired bootstrap p = 0.006 vs ungated), but are significantly
> less grounded than both (p = 0.004, p = 0.023), from one seed per set. Self-hosted on one T4, Lorde costs
> $82.82 per million judgments against an estimated $441.27 at list price for Aya Expanse 32B through the API
> (5×). Model, data, CuratorKIT provenance, Bench sets and a free Kaggle runbook are public.

## Day plan (spec 18.1)

| Day | Work | Exit check |
|---|---|---|
| Mon 9 Nov | Session A: steps 0-2 on Kaggle | `labels_summary.json`: parse-OK ≥ 95%, score bins span low/mid/high |
| Tue 10 Nov | Step 3; hand-label 200 items (step 4); start step 5 | `human_labels.jsonl` complete |
| Wed 11 Nov | Finish training; step 6 raw + structured; 32B calibration | `results/judge_eval.md` |
| Thu 12 Nov | Step 7 plug-in runs; step 8 downstream | `results/plugin_eval.md`, `results/downstream.md` |
| Fri 13 Nov | Step 9 publish (private → review → public); Space; `docs/build_article.py`; HF Article | all repos public, un-gated |
| Sat 14 Nov | Submit early; buffer for fixes | submission confirmed |
