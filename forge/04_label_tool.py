#!/usr/bin/env python
"""Step 4: hand-label the human sample with the same rubric the gates use.

    python forge/04_label_tool.py --labeler lino           # http://localhost:7860
    python forge/04_label_tool.py --labeler lino --share   # public link, e.g. for a phone

Labels are appended to data/human_labels.jsonl (one row per item; the latest
row for an item wins), so you can stop and come back at any time.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import config, jsonl  # noqa: E402

REWARD_RUBRIC = """**Reward rubric (RewardGate).** Score the response 0.0-1.0 overall, considering
*helpfulness* (is it useful?), *honesty* (does it admit uncertainty?) and
*instruction following* (does it address every part?).
0.9-1.0 excellent · 0.7-0.8 good, minor issues · 0.5-0.6 adequate with gaps ·
0.3-0.4 poor · 0.0-0.2 largely unhelpful or wrong. **Pass = 0.7 or higher.**"""

GROUNDING_RUBRIC = """**Grounding rubric (HallucinationGate).** Is every claim in the answer supported
by the source text? Claims from outside knowledge count as unsupported even if true.
1.0 all supported · 0.7-0.9 mostly, minor phrasing · 0.4-0.6 some unsupported ·
0.0-0.3 relies on outside knowledge or contradicts the source. **Pass = 0.7 or higher.**"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", default=str(config.DATA / "human_sample.jsonl"))
    ap.add_argument("--out", default=str(config.DATA / "human_labels.jsonl"))
    ap.add_argument("--labeler", default="anon")
    ap.add_argument("--share", action="store_true")
    ap.add_argument("--port", type=int, default=7860)
    args = ap.parse_args()

    import gradio as gr

    items = jsonl.read(args.sample)
    if not items:
        sys.exit("no human sample: run forge/03_make_splits.py first")
    out_path = Path(args.out)

    def labeled() -> set[str]:
        return {r["uid"] for r in jsonl.iter_rows(out_path)}

    def next_index(start: int = 0) -> int:
        done = labeled()
        for k in range(len(items)):
            i = (start + k) % len(items)
            if items[i]["uid"] not in done:
                return i
        return -1

    def view(i: int):
        done = len(labeled())
        progress = f"**{done} / {len(items)} labeled**"
        if i < 0:
            return (i, progress + " — all done.", "", "", "", gr.update(visible=False, value=None), 0.5, "")
        it = items[i]
        head = f"{progress} · item `{it['uid']}` · {it['kind']} · {it['lang']}"
        if it["kind"] == "reward":
            body = f"### Instruction\n{it['instruction']}\n\n### Response\n{it['response']}"
            rubric, verdict_vis = REWARD_RUBRIC, gr.update(visible=False, value=None)
        else:
            body = (f"### Source text\n{it['source_text']}\n\n### Question\n{it['question']}"
                    f"\n\n### Answer\n{it['answer']}")
            rubric, verdict_vis = GROUNDING_RUBRIC, gr.update(visible=True, value=None)
        return (i, head, rubric, body, it["uid"], verdict_vis, 0.5, "")

    def save(i: int, score: float, verdict: str | None, note: str, labeler: str):
        if i >= 0:
            it = items[i]
            if it["kind"] == "grounding" and not verdict:
                verdict = "grounded" if score >= 0.7 else "partially_grounded" if score >= 0.4 else "hallucinated"
            with jsonl.Appender(out_path) as out:
                out.add({"uid": it["uid"], "kind": it["kind"], "lang": it["lang"],
                         "human_score": round(float(score), 1),
                         "human_verdict": verdict if it["kind"] == "grounding" else None,
                         "labeler": labeler or args.labeler, "note": note or None,
                         "ts": datetime.now(UTC).isoformat()})
        return view(next_index(i + 1))

    def skip(i: int):
        return view(next_index(i + 1))

    with gr.Blocks(title="Lorde · human labels") as demo:
        gr.Markdown("# Lorde · human labels\nLabel each item with the gate's own rubric. Keyboard: Tab to move, Enter to save.")
        idx = gr.State(next_index())
        head = gr.Markdown()
        rubric = gr.Markdown()
        body = gr.Markdown()
        uid = gr.Textbox(visible=False)
        with gr.Row():
            score = gr.Slider(0, 1, value=0.5, step=0.1, label="Score (0.0-1.0)")
            verdict = gr.Radio(["grounded", "partially_grounded", "hallucinated"], label="Verdict")
        note = gr.Textbox(label="Note (optional)")
        labeler = gr.Textbox(value=args.labeler, label="Labeler")
        with gr.Row():
            save_btn = gr.Button("Save and next", variant="primary")
            skip_btn = gr.Button("Skip")
        outs = [idx, head, rubric, body, uid, verdict, score, note]
        demo.load(lambda i: view(i), inputs=idx, outputs=outs)
        save_btn.click(save, inputs=[idx, score, verdict, note, labeler], outputs=outs)
        skip_btn.click(skip, inputs=idx, outputs=outs)

    demo.launch(share=args.share, server_port=args.port)


if __name__ == "__main__":
    main()
