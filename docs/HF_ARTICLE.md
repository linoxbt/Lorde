# Lorde: a 3B judge that runs CuratorKIT's quality gates without breaking their JSON contract

*Lexsi AI Safety & Alignment Hackathon · Track 1 (Fine-tuning and Data Curation) · CuratorKIT → AlignTune → AuditKIT*

**TL;DR.** CuratorKIT filters synthetic training data with two LLM-judge gates. Large API judges charge per
row; small local judges are cheap but break the JSON contract, and CuratorKIT then throws good data away. We
fine-tuned **Tiny Aya Global (3.35B)** to imitate an Aya Expanse 8B teacher's replies to CuratorKIT's *exact*
gate prompts. Lorde's replies are **100.0%** gate-usable (base Tiny Aya Global: 74.2%). On the 937-item held-out test set it agrees with the teacher on **91.1%** of pass/reject decisions (κ = 0.748; base: 64.5%), and on the balanced golden-300 subset on **87.3%** (κ = 0.691). It costs **$82.82 per 1M judgments** on a Tesla T4 vs $441.27 estimated at list price for Aya Expanse 32B through the Cohere API (5×). **That agreement measures imitation, not correctness:** the teacher is a lenient grounding reference (it passes 70% of swapped answers, 94% of fabricated numbers, 76% of closed-book answers and 99% of truncated answers); no human labels have been collected yet; the planned 32B calibration was not run. By its own acceptance criteria Lorde is **not promotable** yet (see *Results*). A QA model trained on Lorde-gated data reaches F1 0.681 (teacher-gated 0.679, ungated 0.664, untrained base 0.492), but its answers are significantly **less grounded** than the ungated set (0.930 vs 0.941, p = 0.004) and teacher-gated (0.930 vs 0.937, p = 0.023); one seed per set, so this is not yet evidence of parity.

## The problem

CuratorKIT's RewardGate (helpfulness, honesty, instruction following) and HallucinationGate (is every claim
supported by the source?) ask a judge for JSON, parse it strictly, and reject the sample when the reply
cannot be parsed (`judge_error:JudgeParseError`). Small open models often wrap the JSON in prose or break
it, so every parse failure deletes a training row that may have been perfectly good. Lorde targets that
failure mode in eight languages, while imitating a larger teacher's pass/reject decisions.

## Approach: chaining three Lexsi toolkits

1. **CuratorKIT** renders every judge prompt (`RewardGate._build_reward_prompt`,
   `HallucinationGate.prompt_template`), parses every reply (`parse_judge_reply`, `extract_score`),
   generates the grounded QA pool from Wikipedia (its QA stage), and finally runs real gating jobs with
   Lorde as `judge_llm`. The judge contract is CuratorKIT's own code, so "gate-usable" means exactly what
   CuratorKIT accepts. Every gated set ships with its `lexsi_provenance.json`.
2. **AlignTune** trains the judge (LoRA SFT, loss on the reply only) and the downstream models.
3. **AuditKIT** scores downstream answers (`f1_score`, `lexical_groundedness`) and runs the paired-bootstrap
   significance tests behind every comparison below.

### Data

- **Reward items:** human-written prompts and answers from `CohereLabs/aya_dataset`, 300 per language.
  aya_dataset's language labels are used as given; some are wrong (at least 10 of the 300 English prompts,
  checked by hand, are in Malay, Hindi, Burmese or Somali).
- **Grounding items:** Wikipedia passages with QA pairs from CuratorKIT's QA stage (teacher-generated).
- Every prompt appears **good and degraded**: the student's own answer, truncation, an off-topic or
  wrong-language answer, padding; swapped answers, fabricated numbers, closed-book answers.
- **Languages:** English, Hindi, French, Arabic, Spanish, Portuguese, Indonesian, Turkish. We chose eight
  languages that the teacher (Aya Expanse, 23 languages) and the student (Tiny Aya Global) *both* support,
  so every label comes from a teacher that actually reads the language.
- **Labels:** Aya Expanse 8B (self-hosted vLLM, fp16 on 2xT4) for every item; the planned Aya Expanse 32B calibration slice was not run; no hand labels have been collected yet (0 of the 200 blind sample items), so human agreement is not measured.
- **Targets:** the teacher's reply re-serialized to the gate's exact keys (canonical JSON, long text
  clipped). Splits are grouped by prompt and source passage: no test prompt or passage appears verbatim in training. Near-duplicates do cross the split, though: **17 of 348** distinct test reward prompts have a training prompt at least 90% similar (normalized text), 0 of 65 test passages do, and **12** test gold responses are identical to a training one (mostly short answers such as a sentiment label). These splits were built before the near-duplicate grouping in `forge/03_make_splits.py`; future builds put near-duplicates in one split.

### Training

LoRA r=16, α=32, dropout 0.05 on all attention and MLP projections; lr 0.0001 cosine, 2 epoch(s) over 5,281 training rows, effective batch 16 (1 x 16 accumulation), max 1,536 tokens (longer rows dropped, never truncated), 6.0 h of training as recorded in train_meta.json: that is the last session only (the code that wrote it did not add earlier sessions of a resumed run), so it is a lower bound. Tiny Aya's 261k-token vocabulary makes the logits the memory peak on a 16 GB T4, hence batch 1. In production, vLLM structured outputs constrain each
gate's reply to its JSON schema, and Bench measures the raw model too so the model itself stays honest.

## Metrics, and why each one

| Metric | Why it matters |
|---|---|
| Gate-usable rate | The failure mode CuratorKIT warns about for small judges: unparseable = data thrown away. |
| Decision agreement, Cohen's κ | The gate only acts on pass/reject at 0.7; κ corrects for skewed pass rates. Both measure agreement with the teacher, i.e. imitation, not correctness. |
| Spearman ρ, MAE | Calibration of scores beyond the threshold. |
| Grounding verdict accuracy | HallucinationGate is the higher-stakes gate (again against the teacher's verdict). |
| Golden-300, adversarial-100 (Bench) | A balanced subset, and items built to fool a judge. |
| Human agreement | The only check on the teacher itself. **Not measured yet: 0 hand labels.** |
| Downstream F1 / groundedness | A judge is only as good as the data it lets through. |
| Latency, throughput, cost per 1M judgments | Pipeline fit and the ROI the hackathon asks for. |

## Results

### What the teacher lets through

Lorde learns the teacher's decisions, so its blind spots matter. Teacher pass rate (score at least 0.7) by item variant; every non-gold variant is degraded on purpose.

| Gate | Variant | Items | Teacher pass rate | Mean score |
|---|---|---|---|---|
| grounding | closed_book | 193 | 76% | 0.72 |
| grounding | fabricated_numbers | 207 | 94% | 0.93 |
| grounding | gold | 944 | 100% | 1.00 |
| grounding | swapped_answer | 174 | 70% | 0.67 |
| grounding | truncated | 234 | 99% | 0.98 |
| reward | gold | 2399 | 91% | 0.81 |
| reward | off_topic | 469 | 3% | 0.30 |
| reward | padded_repetition | 500 | 48% | 0.62 |
| reward | student | 458 | 93% | 0.84 |
| reward | truncated | 503 | 47% | 0.61 |
| reward | wrong_language | 461 | 80% | 0.74 |

**The teacher is a lenient grounding reference.** It passes 70% of swapped answers, 94% of fabricated numbers, 76% of closed-book answers and 99% of truncated answers, all degraded on purpose, and 182 of its 1,752 parseable grounding replies pass an answer while listing an unsupported claim (72 of them say `grounded` with score 1.0). Decision agreement, κ and verdict accuracy below are measured against this teacher, so they measure how faithfully Lorde imitates it, not whether its decisions are right.

**Promotion verdict (spec 9.2): not promotable.** Criteria not met or not measured:

- Cohen's κ vs teacher ≥ 0.75: 0.748
- Human decision agreement ≥ teacher's − 3 pts: not measured: no human labels for test items (forge/04_label_tool.py)
- Per-language decision agreement ≥ 85% (worst: ar, n = 103): 84.5%
- Bench (lorde): Golden decision agreement vs teacher ≥ 90%: 87.3%
- Bench (lorde): Golden κ vs teacher ≥ 0.75: 0.691
- Bench (lorde): Adversarial pass rate ≤ 5%: 46%
- Bench (lorde+so): Golden decision agreement vs teacher ≥ 90%: 86.3%
- Bench (lorde+so): Golden κ vs teacher ≥ 0.75: 0.665
- Bench (lorde+so): Adversarial pass rate ≤ 5%: 47%

### Lorde Bench: judge evaluation

Held-out test set: **937** items (grouped by prompt / source passage, never seen in training). Reference: teacher (Aya Expanse 8B) decisions at threshold 0.7. Unparseable replies count as rejects, as in CuratorKIT. Items whose teacher reply was unparseable have no reference and are excluded (178 over all splits).

Timings: measured on Tesla T4; 256 items per judge, seeded random sample stratified by kind x language, timed before any prediction; 16 concurrent requests.

Templates: curatorkit-0.1.23/reward@fad46b18, curatorkit-0.1.23/hallucination@027e5efc

#### Before / after

| Judge | Gate-usable | Decision agr. | κ | Spearman | MAE | Verdict acc. | Pass rate | p50 s | p95 s | judg/s | $ / 1M |
|---|---|---|---|---|---|---|---|---|---|---|---|
| base | 74.2% | 64.5% | 0.198 | 0.713 | 0.143 | 32.4% | 61.2% | 11.535 | 18.974 | 1.303 | 106.590 |
| **lorde** | 100.0% | 91.1% | 0.748 | 0.864 | 0.058 | 90.9% | 79.1% | 9.280 | 13.290 | 1.677 | 82.820 |
| base+so | 97.8% | 84.7% | 0.559 | 0.481 | 0.151 | 34.8% | 80.2% | 11.361 | 37.174 | 1.133 | 122.590 |
| **lorde+so** | 100.0% | 90.6% | 0.733 | 0.857 | 0.060 | 91.3% | 79.0% | 9.389 | 32.173 | 1.459 | 95.190 |

API reference (an estimate at list price, not a measured cost: the API judge was not called; token counts are the 8B teacher's): c4ai-aya-expanse-32b (Cohere API) at $0.5/1M input and $1.5/1M output tokens, 534.1 prompt + 116.1 completion tokens per judgment (teacher labels (8B tokenizer)) → **$441.27 per 1M judgments**.

Lorde on Tesla T4: **$82.82 per 1M judgments**, 5× cheaper.

#### Acceptance criteria (spec 9.2)

**Promotable: no** (every blocking criterion must be met; unmeasured ones count as not met)

| Criterion | Value | Met | Blocking | Note |
|---|---|---|---|---|
| Gate-usable rate, unconstrained ≥ 99.0% | 100.0% | yes | yes |  |
| Gate-usable rate, structured outputs = 100% | 100.0% | yes | yes |  |
| Decision agreement vs teacher ≥ 90% | 91.1% | yes | yes |  |
| Cohen's κ vs teacher ≥ 0.75 | 0.748 | no | yes |  |
| Spearman ρ ≥ 0.80 (reported) | 0.864 | yes | no |  |
| MAE ≤ 0.10 (reported) | 0.058 | yes | no |  |
| Grounding verdict accuracy ≥ 85% | 90.9% | yes | yes |  |
| Human decision agreement ≥ teacher's − 3 pts | – | not measured | yes | no human labels for test items (forge/04_label_tool.py) |
| Per-language decision agreement ≥ 85% (worst: ar, n = 103) | 84.5% | no | yes |  |
| p95 latency ≤ 1.5 s at 16 concurrent (spec: 1× L4 in production; measured on Tesla T4) | 13.29 s | no | no |  |

Replies that failed: call errors (transport) are retried and never cached; parse failures are replies CuratorKIT could not read (counted as rejects above).

| Judge | Call errors | Parse failures | Missing |
|---|---|---|---|
| base | 0 | 242 | 0 |
| lorde | 0 | 0 | 0 |
| base+so | 0 | 21 | 0 |
| lorde+so | 0 | 0 | 0 |

#### Decision agreement by language

| Judge | ar | en | es | fr | hi | id | pt | tr |
|---|---|---|---|---|---|---|---|---|
| base | 70.9% | 58.0% | 58.9% | 63.0% | 69.9% | 75.7% | 63.2% | 60.3% |
| lorde | 84.5% | 92.0% | 94.4% | 90.7% | 91.1% | 93.2% | 91.2% | 91.4% |
| base+so | 82.5% | 87.7% | 86.9% | 85.2% | 85.0% | 84.5% | 85.1% | 81.5% |
| lorde+so | 83.5% | 91.3% | 93.5% | 90.7% | 90.3% | 94.2% | 90.3% | 90.7% |

#### Pass rate by variant (teacher vs judges)

| Variant | n | teacher | base | lorde | base+so | lorde+so |
|---|---|---|---|---|---|---|
| grounding:closed_book | 22 | 68.2% | 0.0% | 72.7% | 86.4% | 72.7% |
| grounding:fabricated_numbers | 37 | 94.6% | 0.0% | 83.8% | 86.5% | 81.1% |
| grounding:gold | 127 | 100.0% | 0.0% | 100.0% | 97.6% | 100.0% |
| grounding:swapped_answer | 29 | 65.5% | 0.0% | 48.3% | 13.8% | 48.3% |
| grounding:truncated | 26 | 96.2% | 0.0% | 96.2% | 92.3% | 96.2% |
| reward:gold | 348 | 89.4% | 95.1% | 94.0% | 92.2% | 94.0% |
| reward:off_topic | 77 | 2.6% | 26.0% | 5.2% | 19.5% | 5.2% |
| reward:padded_repetition | 58 | 41.4% | 79.3% | 44.8% | 74.1% | 44.8% |
| reward:student | 74 | 90.5% | 94.6% | 94.6% | 94.6% | 96.0% |
| reward:truncated | 71 | 47.9% | 66.2% | 56.3% | 63.4% | 54.9% |
| reward:wrong_language | 68 | 72.1% | 86.8% | 89.7% | 79.4% | 89.7% |

#### Significance (AuditKIT paired bootstrap, decision agreement with teacher)

| Comparison | n | baseline | candidate | Δ | p | significant |
|---|---|---|---|---|---|---|
| lorde vs base | 937 | 64.5% | 91.1% | 26.7% | 0.000999 | yes |
| lorde+so vs base+so | 937 | 84.7% | 90.6% | 5.9% | 0.000999 | yes |

#### Prediction caches

| Judge | Predictions | Weights sha (stamped) | Stamped |
|---|---|---|---|
| base | legacy cache, trusted (`--trust-legacy-cache`) | `af89d219b53e` | 2026-11-11 |
| lorde | legacy cache, trusted (`--trust-legacy-cache`) | `d12ab6ec5c6d` | 2026-11-11 |
| base+so | legacy cache, trusted (`--trust-legacy-cache`) | `af89d219b53e` | 2026-11-11 |
| lorde+so | legacy cache, trusted (`--trust-legacy-cache`) | `d12ab6ec5c6d` | 2026-11-11 |

**base, lorde, base+so, lorde+so:** these predictions were cached by an earlier session, before caches carried a weights fingerprint, and reused here with `--trust-legacy-cache`. This run stamped them with the weights it was serving; nothing proves the earlier session served the same weights. To recompute them, re-run this step without `--trust-legacy-cache` (the old cache is moved aside to `results/preds/<judge>.jsonl.stale` and the server is asked again, 937 items per judge).

### Lorde Bench: golden-300 and adversarial-100

Golden-300 is the balanced Bench subset of the held-out test set (150 reward and 150 grounding items, drawn round-robin over language and variant), so it weighs the degraded variants more than the full test set does. Reference: the teacher.

| Judge | Set (n) | Gate-usable | Decision agr. | κ | MAE |
|---|---|---|---|---|---|
| lorde | full held-out test (937) | 100.0% | 91.1% | 0.748 | 0.058 |
| lorde | golden-300 (300) | 100.0% | 87.3% | 0.691 | 0.086 |
| lorde+so | full held-out test (937) | 100.0% | 90.6% | 0.733 | 0.060 |
| lorde+so | golden-300 (300) | 100.0% | 86.3% | 0.665 | 0.091 |

Adversarial-100 holds prompt injections, forged verdicts, empty fields, extreme lengths and mixed scripts:

- **lorde:** passes **46%** of adversarial-100 items whose reference is *fail* (target ≤ 5%); by attack: empty 60%, extreme length 15%, forged verdict 35%, injection 65%, mixed scripts 55%.
- **lorde+so:** passes **47%** of adversarial-100 items whose reference is *fail* (target ≤ 5%); by attack: empty 60%, extreme length 15%, forged verdict 40%, injection 65%, mixed scripts 55%.

### CuratorKIT plug-in run

Downstream raw set: **3600** grounded QA items (swapped_answer: 362, closed_book: 717, gold: 1822, fabricated_numbers: 527, truncated: 172). Each judge gated it through RewardGate and HallucinationGate at 0.7 inside a real CuratorKIT run.

| Judge | Passed | Pass rate | Judge-error rate | Run time (s) |
|---|---|---|---|---|
| base | 0 | 0.0% | 97.6% | 3704.8 |
| lorde | 2642 | 73.4% | 0.4% | 4096.5 |
| lorde+so | 2659 | 73.9% | 0.0% | 4039.2 |
| teacher | 2299 | 63.9% | 11.4% | 4718.3 |

#### Pass rate by variant

| Judge | closed_book | fabricated_numbers | gold | swapped_answer | truncated |
|---|---|---|---|---|---|
| base | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| lorde | 71.8% | 76.3% | 92.3% | 0.3% | 25.0% |
| lorde+so | 72.8% | 76.7% | 92.6% | 0.3% | 26.2% |
| teacher | 48.7% | 63.8% | 86.4% | 0.0% | 22.7% |

#### Rejection reasons

| Judge | Reasons |
|---|---|
| base | below_min_tokens: 85, judge_error:JudgeParseError: 3515 |
| lorde | below_min_tokens: 85, hallucination_contract_failed: 460, judge_error:JudgeParseError: 13, below_reward_threshold: 400 |
| lorde+so | below_min_tokens: 85, hallucination_contract_failed: 463, below_reward_threshold: 393 |
| teacher | below_min_tokens: 85, hallucination_contract_failed: 280, judge_error:JudgeParseError: 410, below_reward_threshold: 526 |

#### Pairwise decision agreement

| Pair | Agreement | κ |
|---|---|---|
| base vs lorde | 26.6% | 0.000 |
| base vs lorde+so | 26.1% | 0.000 |
| base vs teacher | 36.1% | 0.000 |
| lorde vs lorde+so | 98.4% | 0.959 |
| lorde vs teacher | 86.2% | 0.683 |
| lorde+so vs teacher | 86.2% | 0.682 |

### Downstream test: models trained on gated data

Tiny Aya Base + LoRA trained on each gated set, evaluated on held-out Wikipedia QA (articles never used elsewhere). AuditKIT `f1_score` vs the reference answer, `lexical_groundedness` vs the source passage.

| Training data | Rows | F1 | Groundedness |
|---|---|---|---|
| base | – | 0.492 | 0.908 |
| ungated | 2000 | 0.664 | 0.941 |
| teacher | 2000 | 0.679 | 0.937 |
| lorde | 2000 | 0.681 | 0.930 |

Lorde-gated F1 relative to teacher-gated: **+0.3%** (criterion: within ±2%; met).

Lorde-gated beats ungated on F1 with p < 0.05: **yes**.

Groundedness: Lorde-gated answers are **significantly less grounded** in the source than the ungated set (0.930 vs 0.941, p = 0.004) and teacher-gated (0.930 vs 0.937, p = 0.023).

#### Significance (AuditKIT paired bootstrap)

| Comparison | Metric | baseline | candidate | Δ | p | significant |
|---|---|---|---|---|---|---|
| lorde vs ungated | f1_score | 0.664 | 0.681 | 0.017 | 0.005994 | yes |
| lorde vs ungated | lexical_groundedness | 0.941 | 0.930 | -0.010 | 0.003996 | yes |
| lorde vs teacher | f1_score | 0.679 | 0.681 | 0.002 | 0.703297 | no |
| lorde vs teacher | lexical_groundedness | 0.937 | 0.930 | -0.007 | 0.022977 | yes |
| lorde vs base | f1_score | 0.492 | 0.681 | 0.189 | 0.000999 | yes |
| lorde vs base | lexical_groundedness | 0.908 | 0.930 | 0.022 | 0.002997 | yes |

#### F1 by language

| Training data | ar | en | es | fr | hi | id | pt | tr |
|---|---|---|---|---|---|---|---|---|
| base | 0.448 | 0.495 | 0.434 | 0.506 | 0.579 | 0.508 | 0.478 | 0.486 |
| ungated | 0.583 | 0.696 | 0.672 | 0.670 | 0.674 | 0.695 | 0.710 | 0.609 |
| teacher | 0.571 | 0.716 | 0.701 | 0.679 | 0.684 | 0.710 | 0.738 | 0.633 |
| lorde | 0.610 | 0.704 | 0.674 | 0.697 | 0.691 | 0.732 | 0.721 | 0.622 |

### Reading the downstream result

- **Small study:** one training run (one seed) per set, 1 epoch, 2,000 rows per set, every set subsampled to the same size (settings: forge/kaggle_driver.py DOWNSTREAM_ARGS). The F1 differences between gated sets need more seeds before they can be called parity.
- **Groundedness regresses significantly:** Lorde-gated answers are less grounded in the source than the ungated set (0.930 vs 0.941, p = 0.004) and teacher-gated (0.930 vs 0.937, p = 0.023). In the plug-in run Lorde passed more degraded grounding answers than the teacher (fabricated numbers 76.3% vs 63.8%; closed book 71.8% vs 48.7%), which fits this.
- **The teacher-gated set is depressed by parse errors:** in the plug-in run, 410 of 3,600 rows (11.4%) were rejected because CuratorKIT could not parse the teacher's reply, not because the teacher judged them bad. The "teacher-gated" baseline is therefore the teacher plus its JSON failures.

## ROI: self-hosted Lorde vs paying per token

Scenario: curating a **100,000-row** dataset through both gates = **200,000 judgments**.

|  | Aya Expanse 32B via Cohere API (list-price estimate) | Lorde on 1× Tesla T4 (measured) |
|---|---|---|
| Price | $0.5/1M in, $1.5/1M out (list price) | $0.5/GPU-hour |
| Tokens per judgment | 534.1 in + 116.1 out (the 8B teacher's token counts; the API was not called) | 836.6 in + 92.6 out |
| Throughput | – | 1.677 judgments/s, 155.3 output tokens/s (measured, 16 concurrent; stratified random sample, timed before any prediction) |
| GPU runtime | – | 33.13 h |
| Cost for this dataset | $88.25 (estimate) | $16.56 |
| Cost per 1M judgments | $441.27 (estimate) | $82.82 |

**5× cheaper** at equal volume, on the hardware the throughput was measured on (1× Tesla T4, $0.5/h). The API side is list price times token counts, not a measured bill. Other GPUs will differ; we did not measure them. Cheaper is only useful at the agreement levels above.

## Deviations from the spec

We changed a few things from the build spec (LORDE-SPEC-001 v1.0), on purpose:

- **Languages:** Portuguese, Indonesian and Turkish instead of Swahili, Yoruba and Hausa. Aya Expanse, the
  teacher, does not cover those three, and a teacher that cannot read a language cannot label it.
- **Maximum length 1,536 tokens** instead of 2,048, and **batch 1 × 16 accumulation** instead of 2 × 8. On a
  16 GB T4, Tiny Aya's 261k-token vocabulary makes the logits the memory peak. 1,536 tokens is the 99.7th
  percentile of the judge SFT set, and the effective batch is still 16. Longer rows are dropped, never truncated.
- **Hardware:** everything ran on free Kaggle T4s, not the spec's L4. Every throughput and latency figure
  above names the GPU it was measured on. We did not measure an L4.

## Limitations

- **Lorde imitates an 8B teacher and inherits its blind spots.** On grounding it is lenient (it passes 70% of swapped answers, 94% of fabricated numbers, 76% of closed-book answers and 99% of truncated answers). Human agreement, the only check on the teacher itself, has not been measured (0 hand labels), and the planned 32B calibration slice was not run. High agreement with the teacher is not evidence that the decisions are right.
- **The headline is the easy number.** On the balanced golden-300 subset Lorde agrees with the teacher on 87.3% (κ = 0.691), against 91.1% (κ = 0.748) on the full test set.
- **Judged content can instruct the judge, and Lorde does not resist it:** on adversarial-100 it passes 46% of items whose reference says *fail* (47% with structured outputs); target ≤ 5%, weakest on injection (65%) and empty (60%). High-stakes datasets still need a human in the loop.
- **It misses its own acceptance criteria and is not promotable** (full list under *Results*): Cohen's κ vs teacher ≥ 0.75 (0.748); Human decision agreement ≥ teacher's − 3 pts (not measured); Per-language decision agreement ≥ 85% (84.5%, Arabic); golden-300 agreement ≥ 90% and κ ≥ 0.75 (87.3%, κ = 0.691); adversarial pass rate ≤ 5% (46%).
- **Near-duplicate leakage:** 17 of 348 test reward prompts have a ≥ 90%-similar training prompt and 12 test gold responses equal a training one.
- **The downstream study is small** (one seed, one training run per set, capped rows), groundedness regresses significantly, and the teacher-gated baseline is depressed by the teacher's parse errors (see *Reading the downstream result*).
- It is trained on CuratorKIT's default RewardGate and HallucinationGate templates. Other rubrics are out of distribution; template fingerprints flag them.
- **Prediction provenance:** The step-6 predictions behind the judge numbers (base, lorde, base+so, lorde+so) are a cache written by an earlier Kaggle session, before caches carried a weights fingerprint, and reused with `--trust-legacy-cache`; the run that reported them stamped them with the weights it served (`af89d219b53e`, `d12ab6ec5c6d`), which does not prove the earlier session served the same weights. Re-running step 6 without the flag recomputes them.

## Reproduce it

Everything runs on free Kaggle 2×T4 sessions: `forge/kaggle_runbook.ipynb` in the GitHub repo runs the steps of
`forge/kaggle_driver.py`, with the downstream settings reported above. Model, data (with CuratorKIT manifests and
provenance), Bench sets and the result files are on the Hub. To recompute the step-6 predictions instead of reusing the cached ones, run step 6 (the driver's Session D `judge-timing` step) without `LORDE_TRUST_LEGACY_CACHE=1`; see the runbook.

## Licences

Lorde's weights are CC-BY-NC-4.0 (inherited from Tiny Aya Global; Cohere Labs Acceptable Use Policy applies).
CuratorKIT (LSAL v1.1) and AuditKIT (LSAL v1.2) are non-commercial source-available; AlignTune is MIT. The data
includes Wikipedia excerpts under CC BY-SA 4.0, whose terms continue to apply to them. Lorde is for research and
non-commercial use. See `THIRD_PARTY_LICENSES.md` in the GitHub repo.
