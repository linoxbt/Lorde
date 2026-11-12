"""Lorde demo Space: judge one item with Lorde and see exactly what CuratorKIT would do.

Backends
  LORDE_ENDPOINT set  -> any OpenAI-compatible server running Lorde (e.g. vLLM); supports structured outputs
  otherwise           -> transformers on ZeroGPU (or CPU, slowly), model LORDE_MODEL @ LORDE_REVISION
LORDE_COMPARE_BASE=1 also loads Tiny Aya Global (needs HF_TOKEN with access to the gated base) to compare.

LORDE_REVISION is the release tag to serve (default: DEFAULT_REVISION, never 'main'). At start the app compares
CuratorKIT's current template fingerprints with the model's template_fingerprints.json at that revision and
shows a warning if they differ (the prompts would be out of distribution for Lorde).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gradio as gr  # noqa: E402

from lorde_forge import contract  # noqa: E402
from lorde_forge.config import JUDGE_MAX_TOKENS, JUDGE_TEMPERATURE, STUDENT_MODEL  # noqa: E402
from lorde_forge.schemas import response_format  # noqa: E402

DEFAULT_REVISION = "v1.0.0"  # the published release tag (forge/09_publish.py --tag)
MODEL = os.environ.get("LORDE_MODEL", "Oxlino/lorde-tiny-aya")  # the published model; override per Space
REVISION = os.environ.get("LORDE_REVISION") or DEFAULT_REVISION
if REVISION == "main":  # a moving branch: the demo could silently serve weights nobody evaluated
    print(f"[space] LORDE_REVISION=main is not allowed; serving {DEFAULT_REVISION}", flush=True)
    REVISION = DEFAULT_REVISION
ENDPOINT = os.environ.get("LORDE_ENDPOINT")
DATA_REPO = os.environ.get("LORDE_DATA")
COMPARE = os.environ.get("LORDE_COMPARE_BASE") == "1"

try:
    import spaces

    gpu = spaces.GPU(duration=60)
except Exception:  # noqa: BLE001  (not on ZeroGPU)
    def gpu(fn):
        return fn

_models: dict = {}


def _load(name: str, revision: str = "main"):
    if name not in _models:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
        tok = AutoTokenizer.from_pretrained(name, revision=revision)
        model = AutoModelForCausalLM.from_pretrained(name, revision=revision, dtype=dtype)
        _models[name] = (tok, model)
    return _models[name]


@gpu
def _generate_local(names: list[str], prompt: str) -> list[str]:
    import torch

    outs = []
    for name in names:
        tok, model = _load(name, REVISION if name == MODEL else "main")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model.to(device)
        ids = tok.apply_chat_template([{"role": "user", "content": prompt}], add_generation_prompt=True,
                                      return_tensors="pt", return_dict=True).to(device)
        with torch.no_grad():
            gen = model.generate(**ids, max_new_tokens=JUDGE_MAX_TOKENS, do_sample=True,
                                 temperature=JUDGE_TEMPERATURE)
        outs.append(tok.decode(gen[0, ids["input_ids"].shape[1]:], skip_special_tokens=True))
    return outs


def _generate_endpoint(prompt: str, kind: str, structured: bool) -> str:
    from openai import OpenAI

    client = OpenAI(base_url=ENDPOINT, api_key=os.environ.get("LORDE_API_KEY", "EMPTY"))
    extra = {"response_format": response_format(kind)} if structured else None
    r = client.chat.completions.create(model=os.environ.get("LORDE_SERVED_NAME", "lorde"),
                                       messages=[{"role": "user", "content": prompt}],
                                       temperature=JUDGE_TEMPERATURE, max_tokens=JUDGE_MAX_TOKENS, extra_body=extra)
    return r.choices[0].message.content or ""


def template_warning(trained: dict | None = None) -> str:
    """Markdown warning if CuratorKIT's templates here differ from the ones Lorde was trained on ('' if equal)."""
    now = contract.fingerprints()
    if trained is None:
        try:
            from huggingface_hub import hf_hub_download

            trained = json.loads(Path(hf_hub_download(MODEL, "template_fingerprints.json", revision=REVISION))
                                 .read_text())
        except Exception as e:  # noqa: BLE001  (offline, private repo, older release)
            return (f"**Warning:** could not read `template_fingerprints.json` of `{MODEL}` @ `{REVISION}` "
                    f"({type(e).__name__}), so the prompt templates are not verified.")
    diff = [k for k in contract.KINDS
            if (trained.get("templates", {}).get(k) or {}).get("sha256") != now["templates"][k]["sha256"]]
    if not diff:
        return ""
    return (f"**Warning: template mismatch** for {', '.join(diff)}. Lorde was trained on CuratorKIT "
            f"{trained.get('curatorkit_version', '?')} templates; this Space renders CuratorKIT "
            f"{now['curatorkit_version']}. Its judgments here are out of distribution.")


TEMPLATE_WARNING = template_warning()
if TEMPLATE_WARNING:
    print(f"[space] {TEMPLATE_WARNING}", flush=True)


def explain(kind: str, raw: str, threshold: float) -> str:
    j = contract.parse_reply(kind, raw)
    if not j.ok:
        return ("**Not gate-usable.** CuratorKIT's parser could not read a score, so with the default "
                "`judge_on_error=\"reject\"` the sample is **rejected** as `judge_error:JudgeParseError`. "
                "Good data thrown away.")
    verdict = f", verdict `{j.verdict}`" if j.verdict else ""
    if j.decision(threshold):
        return f"**PASS**: score {j.score:.2f} ≥ {threshold:.2f}{verdict}. CuratorKIT keeps the sample."
    reason = "below_reward_threshold" if kind == "reward" else "hallucination_contract_failed"
    return f"**REJECT**: score {j.score:.2f} < {threshold:.2f}{verdict}. CuratorKIT drops it (`{reason}`)."


def judge(kind, instruction, response, source, question, answer, threshold, structured):
    item = ({"kind": "reward", "instruction": instruction, "response": response} if kind == "Reward" else
            {"kind": "grounding", "source_text": source, "question": question, "answer": answer})
    k = item["kind"]
    prompt = contract.render(item)
    t0 = time.time()
    if ENDPOINT:
        raws = [_generate_endpoint(prompt, k, structured)]
    else:
        names = [MODEL] + ([STUDENT_MODEL] if COMPARE else [])
        raws = _generate_local(names, prompt)
    dt = time.time() - t0
    lorde_raw = raws[0]
    base_raw = raws[1] if len(raws) > 1 else None
    out = [lorde_raw, explain(k, lorde_raw, threshold),
           base_raw or "_Set LORDE_COMPARE_BASE=1 to compare with Tiny Aya Global._",
           explain(k, base_raw, threshold) if base_raw is not None else "",
           f"Template `{contract.template_id(k)}` · {dt:.1f} s", prompt]
    return out


def results_md() -> str:
    if not DATA_REPO:
        return "_Set LORDE_DATA to the data repo to show Lorde Bench results here._"
    try:
        from huggingface_hub import hf_hub_download

        parts = []
        for f in ("results/judge_eval.md", "results/plugin_eval.md", "results/downstream.md"):
            try:
                parts.append(Path(hf_hub_download(DATA_REPO, f, repo_type="dataset")).read_text())
            except Exception:  # noqa: BLE001
                pass
        return "\n\n".join(parts) or "_No results published yet._"
    except Exception as e:  # noqa: BLE001
        return f"_Could not load results: {e}_"


EX_SOURCE = ("The Niger River is the main river of West Africa, extending about 4,180 km. Its drainage basin is "
             "2,117,700 km² in area. Its source is in the Guinea Highlands in south-eastern Guinea.")

with gr.Blocks(title="Lorde: a 3B judge for trustworthy training data") as demo:
    gr.Markdown("# Lorde\nTiny Aya Global (3.35B) fine-tuned to run CuratorKIT's **RewardGate** and "
                "**HallucinationGate**. Enter an item: Lorde judges it with CuratorKIT's exact prompt, and you see "
                "what CuratorKIT would do with the reply.")
    if TEMPLATE_WARNING:
        gr.Markdown(TEMPLATE_WARNING)
    with gr.Tab("Playground"):
        kind = gr.Radio(["Grounding", "Reward"], value="Grounding", label="Gate")
        with gr.Group(visible=True) as g_box:
            source = gr.Textbox(EX_SOURCE, label="Source text", lines=4)
            question = gr.Textbox("How long is the Niger River?", label="Question")
            answer = gr.Textbox("It is about 6,650 km long.", label="Answer")
        with gr.Group(visible=False) as r_box:
            instruction = gr.Textbox("Explain in two sentences why the sky is blue.", label="Instruction")
            response = gr.Textbox("Sunlight scatters off air molecules, and shorter blue wavelengths scatter the "
                                  "most (Rayleigh scattering). So blue light reaches our eyes from every part of "
                                  "the sky.", label="Response", lines=3)
        with gr.Row():
            threshold = gr.Slider(0, 1, value=0.7, step=0.05, label="Threshold (CuratorKIT default 0.7)")
            structured = gr.Checkbox(value=bool(ENDPOINT), interactive=bool(ENDPOINT),
                                     label="Structured outputs (endpoint backend only)")
        btn = gr.Button("Judge", variant="primary")
        with gr.Row():
            with gr.Column():
                gr.Markdown("### Lorde")
                lorde_raw = gr.Code(label="Raw reply", language="json")
                lorde_dec = gr.Markdown()
            with gr.Column():
                gr.Markdown("### Tiny Aya Global (base)")
                base_raw = gr.Code(label="Raw reply", language="json")
                base_dec = gr.Markdown()
        meta = gr.Markdown()
        with gr.Accordion("Rendered CuratorKIT prompt", open=False):
            prompt = gr.Code(language=None)
        kind.change(lambda k: (gr.update(visible=k == "Grounding"), gr.update(visible=k == "Reward")),
                    kind, [g_box, r_box])
        btn.click(judge, [kind, instruction, response, source, question, answer, threshold, structured],
                  [lorde_raw, lorde_dec, base_raw, base_dec, meta, prompt])
    with gr.Tab("Lorde Bench"):
        res = gr.Markdown()
        demo.load(results_md, None, res)
    with gr.Tab("About"):
        gr.Markdown(f"""Model: `{MODEL}` @ `{REVISION}` · templates: {", ".join(
            "`" + contract.template_id(k) + "`" for k in contract.KINDS)}

Built for the Lexsi AI Safety & Alignment Hackathon (Track 1) by chaining CuratorKIT → AlignTune → AuditKIT.
Licence: CC-BY-NC-4.0 (inherited from Tiny Aya Global); non-commercial use only. Judged content is treated as
untrusted text and never executed.""")

if __name__ == "__main__":
    demo.launch(theme=gr.themes.Base())
