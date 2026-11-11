#!/usr/bin/env bash
# Push and start the Lorde kernel on Kaggle (GPU T4 x2, Internet on): bash forge/kaggle/push.sh a|b|ab|c
# Needs the kaggle CLI (>= 1.8) logged in and a private dataset <kaggle-user>/lorde-secrets holding hf_token.
# Optional: LORDE_REPO (git URL) and LORDE_BRANCH override the repo and branch the kernel clones.
set -euo pipefail
cd "$(dirname "$0")"
SESSION="${1:-a}"
KUSER="${KAGGLE_USER:-$(kaggle config view | awk '/username/{print $3}')}"
work=$(mktemp -d)
sed -e "s/^SESSION = .*/SESSION = \"$SESSION\"/" \
    ${LORDE_REPO:+-e "s|\"https://github.com/[^\"]*\"|\"$LORDE_REPO\"|"} \
    ${LORDE_BRANCH:+-e "s|(\"LORDE_BRANCH\", \"[^\"]*\")|(\"LORDE_BRANCH\", \"$LORDE_BRANCH\")|"} \
    kernel.py > "$work/kernel.py"
cat > "$work/kernel-metadata.json" <<JSON
{
  "id": "$KUSER/lorde-forge",
  "title": "lorde-forge",
  "code_file": "kernel.py",
  "language": "python",
  "kernel_type": "script",
  "is_private": true,
  "enable_gpu": true,
  "enable_internet": true,
  "machine_shape": "NvidiaTeslaT4",
  "dataset_sources": ["$KUSER/lorde-secrets"],
  "competition_sources": [],
  "kernel_sources": []
}
JSON
kaggle kernels push -p "$work"
rm -rf "$work"
