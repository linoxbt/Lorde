"""render_chat: AlignTune flattens chat rows, so we apply the chat template ourselves (no torch needed)."""

from lorde_forge.train import Recipe, render_chat


class FakeTok:
    bos_token, bos_token_id, eos_token = "<BOS>", 1, "<EOT>"

    def __call__(self, text):
        return {"input_ids": [1, 7]}  # adds BOS, like the Cohere tokenizers

    def apply_chat_template(self, msgs, tokenize=False, add_generation_prompt=False):
        out = "<BOS><SYS>preamble<EOT>"
        for m in msgs:
            out += f"<{m['role'].upper()}>{m['content']}<EOT>"
        return out + ("<ASSISTANT>" if add_generation_prompt else "")


def test_render_chat_strips_bos_and_keeps_end_tokens():
    tok = FakeTok()
    rows = [{"prompt": [{"role": "user", "content": "rate"}],
             "completion": [{"role": "assistant", "content": '{"overall_score": 0.8}'}]}]
    # FakeTok renders the assistant turn as "<ASSISTANT>...<EOT>", so the generation prompt is a prefix
    out = render_chat(rows, tok)[0]
    assert out["prompt"] == "<SYS>preamble<EOT><USER>rate<EOT><ASSISTANT>"
    assert out["completion"] == '{"overall_score": 0.8}<EOT>'


def test_render_chat_passes_plain_strings_through():
    rows = [{"prompt": "Q: x\nAnswer:", "completion": " y"}]
    assert render_chat(rows, FakeTok()) == rows


def test_recipe_warmup():
    assert Recipe().warmup_steps(1600) == 6  # 100 steps/epoch x 2 epochs x 3%
