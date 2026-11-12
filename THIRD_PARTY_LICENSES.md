# Third-party licences

Lorde's own code in this repository is MIT (see `LICENSE`). The models, data and toolkits it builds on keep
their own terms, summarised here as found in their model and dataset cards, licence files and package
metadata (checked November 2026). This is a summary, not legal advice: the linked terms are authoritative.
Any commercial use needs commercial licences from Cohere and Lexsi Labs first.

## Models

| Component | Used for | Licence |
|---|---|---|
| `CohereLabs/tiny-aya-global` | the student: Lorde's weights are a fine-tune of it | [CC-BY-NC-4.0](https://cohere.com/c4ai-cc-by-nc-license) and the [Cohere Labs Acceptable Use Policy](https://docs.cohere.com/docs/c4ai-acceptable-use-policy) |
| `CohereLabs/tiny-aya-global-GGUF` | the base model served on CPU by the live Gateway | CC-BY-NC-4.0 and the Cohere Labs Acceptable Use Policy |
| `CohereLabs/tiny-aya-base` | base of the downstream-test models (step 8) | CC-BY-NC-4.0 and the Cohere Labs Acceptable Use Policy |
| `CohereLabs/aya-expanse-8b` | the teacher: its replies are Lorde's training targets; it also generated the grounded QA pairs and the translations of the wrong-language variant | CC-BY-NC-4.0 and the Cohere Labs Acceptable Use Policy |
| `c4ai-aya-expanse-32b` (Cohere API) | planned calibration slice and the ROI price reference; not called for v1 | Cohere's API terms |

**Lorde (`<user>/lorde-tiny-aya`) is therefore CC-BY-NC-4.0 and subject to the Cohere Labs Acceptable Use
Policy:** non-commercial use only. The Hugging Face repos are un-gated for the hackathon; the terms still apply.

## Data

| Source | In the published dataset (`<user>/lorde-data`) | Licence |
|---|---|---|
| `CohereLabs/aya_dataset` | reward prompts and human-written answers, and variants derived from them | Apache-2.0 |
| Wikipedia via `wikimedia/wikipedia` (dump `20231101`) | source passages of the grounding items and the downstream QA set, and text derived from them | Wikipedia text is CC BY-SA 4.0 (CC BY-SA 3.0 for older revisions; most text is also GFDL); the `wikimedia/wikipedia` card lists `cc-by-sa-3.0` and `gfdl` |
| Aya Expanse 8B outputs | teacher labels, generated questions and answers, translations | CC-BY-NC-4.0 model terms and the Cohere Labs Acceptable Use Policy |
| Tiny Aya Global outputs | student answers (reward items) and closed-book answers (grounding items) | CC-BY-NC-4.0 model terms and the Cohere Labs Acceptable Use Policy |

**The dataset includes CC BY-SA content** (Wikipedia excerpts and text derived from them). The CC BY-SA terms
(attribution to Wikipedia's contributors, share-alike for adaptations) continue to apply to that content,
alongside the non-commercial terms that apply to the model outputs.

## Toolkits and libraries

| Package | Used for | Licence |
|---|---|---|
| CuratorKIT 0.1.23 (Lexsi Labs) | judge prompts and parser, QA stage, gating runs; the Gateway | Lexsi Labs Source Available License (LSAL) v1.1: noncommercial purposes only, plus its Responsible Use conditions; not an OSI open-source licence |
| AuditKIT 1.1.6 (Lexsi Labs) | downstream scoring and paired-bootstrap significance tests | Lexsi Labs Source Available License (LSAL) v1.2: noncommercial purposes only |
| AlignTune 0.1.32 (Lexsi Labs) | LoRA SFT (TRL backend) | AlignTune's own code is MIT. Its wheel bundles third-party code, so its declared licence expression is `MIT AND Apache-2.0 AND AGPL-3.0-only AND LGPL-3.0-only` (vendored mergekit is LGPL-3.0; vendored unsloth has AGPL-3.0 parts used only by the optional `unsloth` backend, which Lorde does not use) |
| vLLM, Transformers, TRL, PEFT, Accelerate, Datasets, huggingface_hub, Gradio | serving, training, data access, label tool | Apache-2.0 |
| PyTorch | training and inference | BSD-3-Clause |
| llama.cpp (Railway CPU serving) | the live model server | MIT |

Every other Python and JavaScript dependency is used under the licence declared in its own package metadata.
