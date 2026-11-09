# Hackathon submission checklist

Deadline: **14 Nov 2026, end of day AoE**. Your latest submission counts, so submit early and resubmit.
Shortlist announced 16 Nov; Demo Day 22 Nov (shortlisted teams get $200 in HF credits for 16-21 Nov).

## Rules (hackathon.lexsi.ai) → where Lorde meets them

| Rule | Lorde | Status |
|---|---|---|
| Track 1: chain ≥ 2 repos, 3+ preferred | CuratorKIT → AlignTune → AuditKIT (all three) | ☐ |
| Cohere models only (core) | Tiny Aya Global (student), Aya Expanse 8B/32B (teacher); others only as reference judges | ☐ |
| All artifacts on Hugging Face | model, adapters/merged weights, data, labels, logs, results | ☐ |
| Deployed publicly | Space `<user>/lorde-demo` + public GitHub repo | ☐ |
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
  - Models: Tiny Aya Global, Tiny Aya Base (downstream), Aya Expanse 8B, Aya Expanse 32B
  - Track 1 · problem statements: *Replacing a closed-model API*, *Data-scarce domains*
- [ ] Keep the Kaggle notebook and `results/` for the thesis week (16-21 Nov)

## Abstract (draft, fill the bracketed numbers from results/)

> **Lorde: a 3B judge that keeps CuratorKIT's quality gates honest.** CuratorKIT curates training data with two
> LLM-judge gates, RewardGate and HallucinationGate. It parses the judge's JSON strictly and rejects a sample
> whenever the reply cannot be parsed. Large API judges are reliable but charge per row; small local judges are
> cheap but break the JSON contract and throw good data away. We fine-tuned Tiny Aya Global (3.35B) to be that
> judge. Using CuratorKIT's own prompt builders and parser as the contract, we built a pool of [N] reward and
> grounding items in eight languages (aya_dataset prompts and Wikipedia QA from CuratorKIT's QA stage), each
> shown good and degraded. We labeled it with Aya Expanse 8B, calibrated on Aya Expanse 32B through the Cohere
> API and 200 hand labels, and trained a LoRA with AlignTune on canonical JSON targets. On a held-out test set
> grouped by prompt, Lorde's replies are [x]% gate-usable (base: [y]%; 100% with vLLM structured outputs), agree
> with the teacher on [z]% of pass/reject decisions (κ = [k]), and stay within [d] points of the teacher's own
> agreement with humans across all eight languages. Plugged into real CuratorKIT runs, Lorde's judge-error rate
> drops from [a]% to [b]%. Models trained on Lorde-gated data match teacher-gated data ([F1], within [r]%) and
> beat ungated data (AuditKIT paired bootstrap, p = [p]). Self-hosted on one L4, Lorde costs [$c] per million
> judgments against [$api] for Aya Expanse 32B through the API ([m]× cheaper). Everything (model, data,
> CuratorKIT provenance, Bench sets and a free Kaggle runbook) is public.

## Day plan (spec 18.1)

| Day | Work | Exit check |
|---|---|---|
| Mon 9 Nov | Session A: steps 0-2 on Kaggle | `labels_summary.json`: parse-OK ≥ 95%, score bins span low/mid/high |
| Tue 10 Nov | Step 3; hand-label 200 items (step 4); start step 5 | `human_labels.jsonl` complete |
| Wed 11 Nov | Finish training; step 6 raw + structured; 32B calibration | `results/judge_eval.md` |
| Thu 12 Nov | Step 7 plug-in runs; step 8 downstream | `results/plugin_eval.md`, `results/downstream.md` |
| Fri 13 Nov | Step 9 publish (private → review → public); Space; `docs/build_article.py`; HF Article | all repos public, un-gated |
| Sat 14 Nov | Submit early; buffer for fixes | submission confirmed |
