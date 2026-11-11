"""Demo Space (audit M15): pinned revision, template fingerprint check at start."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytest.importorskip("gradio")


def test_space_pins_revision_and_checks_templates():
    code = ("import sys; sys.path.insert(0, 'space'); import app\n"
            "from lorde_forge import contract\n"
            "print('REV', app.REVISION)\n"
            "print('OFFLINE', 'could not read' in app.TEMPLATE_WARNING)\n"
            "fp = contract.fingerprints()\n"
            "print('SAME', app.template_warning(fp) == '')\n"
            "fp['templates']['reward']['sha256'] = '0' * 64\n"
            "print('DIFF', 'template mismatch' in app.template_warning(fp))\n")
    env = {**os.environ, "LORDE_REVISION": "main", "HF_HUB_OFFLINE": "1", "PYTHONPATH": str(ROOT)}
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr[-2000:]
    out = dict(line.split(" ", 1) for line in p.stdout.splitlines() if line[:4] in ("REV ", "OFFL", "SAME", "DIFF"))
    assert out["REV"] == "v1.0.0"
    assert out["OFFLINE"] == "True" and out["SAME"] == "True" and out["DIFF"] == "True"
    assert "curatorkit==0.1.23" in (ROOT / "space" / "requirements.txt").read_text()
