#!/usr/bin/env python
"""Step 10 (optional): export the merged Lorde model to GGUF and push it to the Hub for CPU serving.

    python forge/10_export_gguf.py --user <hf-user>              # private repo <user>/lorde-tiny-aya-GGUF
    python forge/10_export_gguf.py --user <hf-user> --public

Uses llama.cpp's own converter (convert_hf_to_gguf.py, cloned at run time at the pinned tag LLAMA_CPP_REF,
overridable with --llama-cpp-ref or the env var of the same name) with --outtype q8_0, which
needs no compiled tools. The staging Serve on Railway (llama.cpp) then serves it by setting
LLAMA_ARG_HF_REPO=<user>/lorde-tiny-aya-GGUF:Q8_0. Runs in the forge-train env (torch, transformers).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import config  # noqa: E402

LLAMA_CPP = "https://github.com/ggml-org/llama.cpp"
LLAMA_CPP_REF = os.environ.get("LLAMA_CPP_REF", "b12283")  # a release tag, never HEAD: the converter changes often

CARD = """---
license: cc-by-nc-4.0
base_model: {user}/lorde-tiny-aya
tags: [gguf, llm-judge, curatorkit, lexsi-hackathon]
---

# Lorde (GGUF)

GGUF build of [{user}/lorde-tiny-aya](https://huggingface.co/{user}/lorde-tiny-aya) for llama.cpp and CPU serving.
Quantization: {outtype}, converted with llama.cpp `{ref}`. Serve with:

```bash
llama-server -hf {user}/lorde-tiny-aya-GGUF:{tag} --alias lorde --jinja -c 4096
```

Use structured outputs per gate (`response_format` with the gate's JSON schema) so every reply is gate-usable.
Licence: CC-BY-NC-4.0, inherited from Tiny Aya Global, and the Cohere Labs Acceptable Use Policy
(https://docs.cohere.com/docs/c4ai-acceptable-use-policy). Non-commercial use only.
"""


def eos_token_id(model_dir: Path) -> int:
    """Id of Tiny Aya's ``<EOS_TOKEN>`` in the model's tokenizer.json.

    The converter takes the GGUF EOS from tokenizer_config's ``eos_token``, which transformers saves as
    ``<|END_OF_TURN_TOKEN|>`` after chat fine-tuning. With that EOS, llama.cpp's chat parser for Cohere templates
    rejects structured replies (HTTP 500) and leaves the turn marker in plain replies, so the export resets EOS to
    ``<EOS_TOKEN>``, as in Cohere's own tiny-aya-global-GGUF.
    """
    tok = json.loads((model_dir / "tokenizer.json").read_text(encoding="utf-8"))
    for t in tok.get("added_tokens", []):
        if t.get("content") == "<EOS_TOKEN>":
            return int(t["id"])
    sys.exit(f"<EOS_TOKEN> not found in {model_dir / 'tokenizer.json'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", required=True)
    ap.add_argument("--model-dir", default=str(config.RUNS / config.RUN_NAME / "merged"))
    ap.add_argument("--outtype", default="q8_0", choices=["q8_0", "f16", "bf16"])
    ap.add_argument("--repo", default="lorde-tiny-aya-GGUF")
    ap.add_argument("--work", default=str(config.RUNS / "gguf"))
    ap.add_argument("--public", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--llama-cpp-ref", default=LLAMA_CPP_REF, help="llama.cpp tag to convert with")
    args = ap.parse_args()

    model_dir = Path(args.model_dir)
    if not (model_dir / "config.json").exists():
        sys.exit(f"no merged model at {model_dir}: run forge/05_train_sft.py")
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    src = work / f"llama.cpp-{args.llama_cpp_ref}"
    if not (src / "convert_hf_to_gguf.py").exists():
        subprocess.run(["git", "clone", "--depth", "1", "--branch", args.llama_cpp_ref, LLAMA_CPP, str(src)],
                       check=True)
    # gguf-py from the same checkout keeps the converter and the library in step.
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", str(src / "gguf-py")], check=True)

    tag = args.outtype.upper()
    out = work / f"lorde-tiny-aya-{tag}.gguf"
    subprocess.run([sys.executable, str(src / "convert_hf_to_gguf.py"), str(model_dir), "--outtype", args.outtype,
                    "--outfile", str(out)], check=True)
    eos = eos_token_id(model_dir)
    subprocess.run([sys.executable, str(src / "gguf-py" / "gguf" / "scripts" / "gguf_set_metadata.py"), str(out),
                    "tokenizer.ggml.eos_token_id", str(eos), "--force"], check=True)
    print(f"[gguf] wrote {out} ({out.stat().st_size / 1e9:.2f} GB), eos_token_id={eos} (<EOS_TOKEN>)")
    if args.no_push:
        return

    from huggingface_hub import HfApi

    api = HfApi()
    repo = f"{args.user}/{args.repo}"
    api.create_repo(repo, private=not args.public, exist_ok=True)
    if not api.file_exists(repo, "README.md"):  # never replace a hand-written card (provenance, checks)
        (work / "README.md").write_text(CARD.format(user=args.user, outtype=args.outtype, tag=tag,
                                                    ref=args.llama_cpp_ref), encoding="utf-8")
        api.upload_file(path_or_fileobj=str(work / "README.md"), path_in_repo="README.md", repo_id=repo)
    api.upload_file(path_or_fileobj=str(out), path_in_repo=out.name, repo_id=repo, commit_message="Lorde GGUF")
    print(f"[gguf] pushed https://huggingface.co/{repo} · serve with LLAMA_ARG_HF_REPO={repo}:{tag}")


if __name__ == "__main__":
    main()
