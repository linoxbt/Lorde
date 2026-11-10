# Lorde: a 3B judge that keeps CuratorKIT's quality gates honest, at a fraction of the cost

*Lexsi AI Safety & Alignment Hackathon · Track 1 (Fine-tuning and Data Curation) · CuratorKIT → AlignTune → AuditKIT*

**TL;DR.** CuratorKIT filters synthetic training data with two LLM-judge gates. Large API judges are reliable
but charge per row; small local judges are cheap but break the JSON contract, and CuratorKIT then throws good
data away. We fine-tuned **Tiny Aya Global (3.35B)** on an Aya Expanse teacher's replies to CuratorKIT's *exact*
gate prompts. <!-- tldr -->

## The problem

CuratorKIT's RewardGate (helpfulness, honesty, instruction following) and HallucinationGate (is every claim
supported by the source?) ask a judge for JSON, parse it strictly, and reject the sample when the reply
cannot be parsed (`judge_error:JudgeParseError`). Small open models often wrap the JSON in prose or break
it, so every parse failure deletes a training row that may have been perfectly good. That is the failure
mode Lorde removes, in eight languages, while matching a larger teacher's pass/reject decisions.

## Approach: chaining three Lexsi toolkits

1. **CuratorKIT** renders every judge prompt (`RewardGate._build_reward_prompt`,
   `HallucinationGate.prompt_template`), parses every reply (`parse_judge_reply`, `extract_score`),
   generates the grounded QA pool from Wikipedia (its QA stage), and finally runs real gating jobs with
   Lorde as `judge_llm`. The judge contract is CuratorKIT's own code, so "gate-usable" means exactly what
   CuratorKIT accepts. Every gated set ships with its `lexsi_provenance.json`.
2. **AlignTune** trains the judge (LoRA SFT, loss on the reply only) and the downstream models.
3. **AuditKIT** scores downstream answers (`f1_score`, `lexical_groundedness`) and runs the paired-bootstrap
   significance tests behind every "better than" claim below.

### Data

- **Reward items:** human-written prompts and answers from `CohereLabs/aya_dataset`, 300 per language.
- **Grounding items:** Wikipedia passages with QA pairs from CuratorKIT's QA stage (teacher-generated).
- Every prompt appears **good and degraded**: the student's own answer, truncation, an off-topic or
  wrong-language answer, padding; swapped answers, fabricated numbers, closed-book answers.
- **Languages:** English, Hindi, French, Arabic, Spanish, Portuguese, Indonesian, Turkish. We chose eight
  languages that the teacher (Aya Expanse, 23 languages) and the student (Tiny Aya Global) *both* support,
  so every label comes from a teacher that actually reads the language.
<!-- labels_note -->
- **Targets:** the teacher's reply re-serialized to the gate's exact keys (canonical JSON, long text
  clipped). Splits are grouped by prompt and source passage, so no test prompt appears in training.

### Training

<!-- train_recipe --> In production, vLLM structured outputs constrain each
gate's reply to its JSON schema, and Bench measures the raw model too so the model itself stays honest.

## Metrics, and why each one

| Metric | Why it matters |
|---|---|
| Gate-usable rate | The failure mode CuratorKIT warns about for small judges: unparseable = data thrown away. |
| Decision agreement, Cohen's κ | The gate only acts on pass/reject at 0.7; κ corrects for skewed pass rates. |
| Spearman ρ, MAE | Calibration of scores beyond the threshold. |
| Grounding verdict accuracy | HallucinationGate is the higher-stakes gate. |
| Human agreement | Keeps the teacher honest: the teacher's own human agreement is the ceiling. |
| Downstream F1 / groundedness | A judge is only as good as the data it lets through. |
| Latency, throughput, cost per 1M judgments | Pipeline fit and the ROI the hackathon asks for. |

## Results

<!-- teacher_audit -->

<!-- results:judge_eval -->

<!-- results:plugin_eval -->

<!-- results:downstream -->

## ROI: self-hosted Lorde vs paying per token

<!-- roi -->

## Limitations

- Lorde copies an 8B teacher and inherits its blind spots; human agreement is reported against that ceiling.
- It is trained on CuratorKIT's default RewardGate and HallucinationGate templates. Other rubrics are out of
  distribution; template fingerprints flag them.
- Judged content can try to instruct the judge; adversarial-100 measures this (see Bench), and high-stakes
  datasets still deserve a human in the loop.

## Reproduce it

Everything runs on a free Kaggle 2×T4 notebook: `forge/kaggle_runbook.ipynb` in the GitHub repo. Model,
data (with CuratorKIT manifests and provenance), Bench sets and every result file are on the Hub.

## Licences

Lorde's weights are CC-BY-NC-4.0 (inherited from Tiny Aya Global; Cohere Labs Acceptable Use Policy applies).
CuratorKIT (LSAL v1.1) and AuditKIT (LSAL v1.2) are non-commercial source-available; AlignTune is MIT.
Lorde is for research and non-commercial use.
