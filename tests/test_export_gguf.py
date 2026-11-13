"""forge/10_export_gguf.py resets the GGUF EOS to Tiny Aya's <EOS_TOKEN> (llama.cpp chat parsing needs it)."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("export_gguf", Path(__file__).parent.parent / "forge" / "10_export_gguf.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_eos_token_id_is_the_eos_token_not_the_turn_end(tmp_path):
    (tmp_path / "tokenizer.json").write_text(json.dumps({"added_tokens": [
        {"id": 3, "content": "<EOS_TOKEN>"}, {"id": 6, "content": "<|END_OF_TURN_TOKEN|>"}]}))
    assert mod.eos_token_id(tmp_path) == 3


def test_eos_token_id_missing_stops(tmp_path):
    (tmp_path / "tokenizer.json").write_text(json.dumps({"added_tokens": [{"id": 6, "content": "<|END_OF_TURN_TOKEN|>"}]}))
    with pytest.raises(SystemExit):
        mod.eos_token_id(tmp_path)
