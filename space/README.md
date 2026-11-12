---
title: Lorde
emoji: ⚖️
colorFrom: indigo
colorTo: gray
sdk: gradio
app_file: app.py
pinned: false
license: cc-by-nc-4.0
short_description: A 3B judge for CuratorKIT's quality gates
---

# Lorde demo

Judge one item with Lorde and see what CuratorKIT would do with the reply. Published by
`forge/09_publish.py --space`, which also uploads `lorde_forge/` next to `app.py`.

Variables: `LORDE_MODEL`, `LORDE_REVISION` (a release tag; default `v1.0.0`, never `main`),
`LORDE_DATA` (data repo for the Bench tab),
`LORDE_ENDPOINT` (optional OpenAI-compatible server), `LORDE_COMPARE_BASE=1` (+ secret `HF_TOKEN`).
