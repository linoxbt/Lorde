"""Process hardening for the Gateway and worker processes (audit 2, S9). Standard library only.

The job child (worker/run_job.py) runs CuratorKIT on tenant data with a minimal environment (worker.main.child_env:
no DATABASE_URL, LORDE_KEY_PEPPER, SUPABASE_* or HF token). It runs as the same uid as the worker and the Gateway,
so without more it could still read their secrets from /proc/<pid>/environ (or ptrace them) if it were ever
compromised. ``make_undumpable()`` sets PR_SET_DUMPABLE to 0 in the calling process: the kernel then makes its
/proc/<pid> files root-owned and refuses ptrace from processes of the same uid. The worker and the Gateway call it
at startup. (A separate uid for the child would need CAP_SETUID in the worker, i.e. a worker able to become any
user, root included; with this flag the child gets no more than its own environment.)
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import sys

PR_SET_DUMPABLE, PR_GET_DUMPABLE = 4, 3


def _prctl(option: int, arg: int = 0) -> int:
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    return int(libc.prctl(option, arg, 0, 0, 0))


def make_undumpable() -> bool:
    """Mark this process non-dumpable (Linux). Returns True when the flag is set; logs and returns False elsewhere."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        if _prctl(PR_SET_DUMPABLE, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_DUMPABLE, 0) failed")
        return _prctl(PR_GET_DUMPABLE) == 0
    except OSError:
        logging.getLogger("lorde.procsec").warning("could not mark the process non-dumpable", exc_info=True)
        return False
