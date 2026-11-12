# Lorde: a 3B judge that runs CuratorKIT's quality gates without breaking their JSON contract

*Lexsi AI Safety & Alignment Hackathon · Track 1 (Fine-tuning and Data Curation) · CuratorKIT → AlignTune → AuditKIT*

**TL;DR.** CuratorKIT filters synthetic training data with two LLM-judge gates. Large API judges charge per
row; small local judges are cheap but break the JSON contract, and CuratorKIT then throws good data away. We
fine-tuned **Tiny Aya Global (3.35B)** to imitate an Aya Expanse 8B teacher's replies to CuratorKIT's *exact*
gate prompts. <!-- tldr -->

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
<!-- labels_note -->
- **Targets:** the teacher's reply re-serialized to the gate's exact keys (canonical JSON, long text
  clipped). <!-- leakage -->

### Training

<!-- train_recipe --> In production, vLLM structured outputs constrain each
gate's reply to its JSON schema, and Bench measures the raw model too so the model itself stays honest.

## Metrics, and why each one

| Metric | Why it matters |
|---|---|
| Gate-usable rate | The failure mode CuratorKIT warns about for small judges: unparseable = data thrown away. |
| Decision agreement, Cohen's κ | The gate only acts on pass/reject at 0.7; κ corrects for skewed pass rates. Both measure agreement with the teacher, i.e. imitation, not correctness. |
| Spearman ρ, MAE | Calibration of scores beyond the threshold. |
| Grounding verdict accuracy | HallucinationGate is the higher-stakes gate (again against the teacher's verdict). |
| Golden-300, adversarial-100 (Bench) | A balanced subset, and items built to fool a judge. |
| Human agreement | The only check on the teacher itself. <!-- human_status --> |
| Downstream F1 / groundedness | A judge is only as good as the data it lets through. |
| Latency, throughput, cost per 1M judgments | Pipeline fit and the ROI the hackathon asks for. |

## Results

<!-- teacher_audit -->

<!-- acceptance -->

<!-- results:judge_eval -->

<!-- bench -->

<!-- results:plugin_eval -->

<!-- results:downstream -->

<!-- downstream_notes -->

## ROI: self-hosted Lorde vs paying per token

<!-- roi -->

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

<!-- limitations -->

## Reproduce it

Everything runs on free Kaggle 2×T4 sessions: `forge/kaggle_runbook.ipynb` in the GitHub repo runs the steps of
`forge/kaggle_driver.py`, with the downstream settings reported above. Model, data (with CuratorKIT manifests and
provenance), Bench sets and the result files are on the Hub. <!-- reproduce_note -->

## Licences

Lorde's weights are CC-BY-NC-4.0 (inherited from Tiny Aya Global; Cohere Labs Acceptable Use Policy applies).
CuratorKIT (LSAL v1.1) and AuditKIT (LSAL v1.2) are non-commercial source-available; AlignTune is MIT. The data
includes Wikipedia excerpts under CC BY-SA 4.0, whose terms continue to apply to them. Lorde is for research and
non-commercial use. See `THIRD_PARTY_LICENSES.md` in the GitHub repo.
