"""Shared test scaffolding: an isolated board per test case."""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import board  # noqa: E402


class BoardTestCase(unittest.TestCase):
    """A fresh, throwaway board file per test — never the operator's own."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="triai-board-"))
        self.db_path = self.tmp / "board.db"
        # Pin every kernel path resolution at this temp board, so nothing can
        # reach the real ~/.hermes boards even if a code path forgets to pass
        # an explicit path.
        self._prev_env = os.environ.get("HERMES_KANBAN_DB")
        os.environ["HERMES_KANBAN_DB"] = str(self.db_path)
        self.kb = board.kanban()
        self.conn = board.connect(self.db_path)
        self.addCleanup(self._teardown)

    def _teardown(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass
        if self._prev_env is None:
            os.environ.pop("HERMES_KANBAN_DB", None)
        else:
            os.environ["HERMES_KANBAN_DB"] = self._prev_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------

    def host_local_claimer(self, pid: int) -> str:
        """A claim lock the kernel will recognise as belonging to this host."""
        return f"{socket.gethostname()}:{int(pid)}"

    def task_row(self, task_id: str):
        return self.conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()

    def event_kinds(self, task_id: str) -> list[str]:
        return [
            r["kind"]
            for r in self.conn.execute(
                "SELECT kind FROM task_events WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
        ]
