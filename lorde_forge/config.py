"""Constants shared by every Forge step. Change them here, not in scripts."""

from __future__ import annotations

import os
from pathlib import Path

# Languages: every one is supported by both the teacher (Aya Expanse lists 23)
# and the student (Tiny Aya Global covers 67+), so teacher labels are trustworthy
# in all of them. Keys are ISO 639-1; values are aya_dataset `language_code`
# (ISO 639-3) and the Wikipedia dump suffix.
LANGS: dict[str, dict[str, str]] = {
    "en": {"aya": "eng", "wiki": "en", "name": "English"},
    "hi": {"aya": "hin", "wiki": "hi", "name": "Hindi"},
    "fr": {"aya": "fra", "wiki": "fr", "name": "French"},
    "ar": {"aya": "arb", "wiki": "ar", "name": "Arabic"},
    "es": {"aya": "spa", "wiki": "es", "name": "Spanish"},
    "pt": {"aya": "por", "wiki": "pt", "name": "Portuguese"},
    "id": {"aya": "ind", "wiki": "id", "name": "Indonesian"},
    "tr": {"aya": "tur", "wiki": "tr", "name": "Turkish"},
}
LANG_CODES = list(LANGS)

WIKI_DUMP = "20231101"
AYA_DATASET = "CohereLabs/aya_dataset"
WIKI_DATASET = "wikimedia/wikipedia"

STUDENT_MODEL = "CohereLabs/tiny-aya-global"
DOWNSTREAM_BASE_MODEL = "CohereLabs/tiny-aya-base"
TEACHER_MODEL = "CohereLabs/aya-expanse-8b"
CALIBRATION_MODEL = "c4ai-aya-expanse-32b"
COHERE_COMPAT_BASE = "https://api.cohere.ai/compatibility/v1"

# CuratorKIT's gate default and its judge role defaults (temperature, max_tokens).
THRESHOLD = 0.7
JUDGE_TEMPERATURE = 0.1
JUDGE_MAX_TOKENS = 512
MAX_SEQ_LEN = 1536  # p99.7 of the judge SFT set; Tiny Aya's 261k vocab makes logits the memory peak on T4

# Reference prices (USD), checked 9 Nov 2026. Replace with measured values in the article.
GPU_HOURLY = {"t4": 0.50, "l4": 0.80}
COHERE_32B_PRICE_PER_M = {"input": 0.50, "output": 1.50}

ROOT = Path(os.environ.get("LORDE_ROOT", Path(__file__).resolve().parent.parent))
DATA = ROOT / "data"
RESULTS = ROOT / "results"
RUNS = ROOT / "runs"
RUN_NAME = "lorde-v1"
SERVED_NAME = "lorde"
