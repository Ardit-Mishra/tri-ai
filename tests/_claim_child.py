"""Child process for the contention test: wait for a shared start time, then
make exactly one claim attempt and report the outcome as JSON on stdout.

Separate processes, not threads: the claim's mutual exclusion is SQLite's
cross-process write lock, and that is what has to be proven.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

db_path, task_id, start_at = sys.argv[1], sys.argv[2], float(sys.argv[3])
os.environ["HERMES_KANBAN_DB"] = db_path

import board  # noqa: E402

kb = board.kanban()
conn = board.connect(Path(db_path))
claimer = f"{__import__('socket').gethostname()}:{os.getpid()}"

# Spin to the shared deadline so every child hits the CAS in the same instant.
while time.time() < start_at:
    time.sleep(0.001)

try:
    task = kb.claim_task(conn, task_id, claimer=claimer)
    result = {"pid": os.getpid(), "claimer": claimer, "claimed": task is not None}
except Exception as exc:  # a crash here is a test failure, not a lost claim
    result = {"pid": os.getpid(), "claimer": claimer, "error": repr(exc)}

sys.stdout.write(json.dumps(result))
sys.stdout.flush()
