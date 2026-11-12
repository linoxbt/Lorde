# Lorde · Kaggle kernel entry point (pushed with `kaggle kernels push -p forge/kaggle`).
# Reads the HF token from the attached private dataset, clones the repo and runs forge/kaggle_driver.py.
# SESSION (and REPO / BRANCH when LORDE_REPO / LORDE_BRANCH are set) are rewritten by forge/kaggle/push.sh;
# at run time the environment variables of the same name win.
import glob
import os
import shutil
import subprocess
import sys

SESSION = "a"
REPO = os.environ.get("LORDE_REPO", "https://github.com/linoxbt/Lorde.git")
BRANCH = os.environ.get("LORDE_BRANCH", "main")

for name in ("hf_token", "cohere_api_key"):
    hits = glob.glob(f"/kaggle/input/**/{name}", recursive=True)
    if hits:
        os.environ[name.upper() if name != "hf_token" else "HF_TOKEN"] = open(hits[0]).read().strip()
if "HF_TOKEN" not in os.environ:
    try:
        from kaggle_secrets import UserSecretsClient

        os.environ["HF_TOKEN"] = UserSecretsClient().get_secret("HF_TOKEN")
    except Exception:
        sys.exit("no HF token: attach the lorde-secrets dataset or add an HF_TOKEN secret")

src = "/tmp/Lorde"
if not os.path.isdir(src):
    subprocess.run(["git", "clone", "-q", "-b", BRANCH, REPO, src], check=True)
subprocess.run(["git", "-C", src, "log", "--oneline", "-1"], check=False)
rc = subprocess.run([sys.executable, "-u", f"{src}/forge/kaggle_driver.py", SESSION]).returncode
for d in ("results", "logs"):
    if os.path.isdir(f"{src}/{d}"):
        shutil.copytree(f"{src}/{d}", f"/kaggle/working/{d}", dirs_exist_ok=True)
sys.exit(rc)
