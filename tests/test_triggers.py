"""Criterion 4 (TRIG-01/02/03): the three assignment triggers write ONE row shape.

Every task that reaches the board goes through board.create_task — the single
write path — so the claim, execution and ledger path downstream is identical by
construction, not by convention. This test pins that construction: a task
created by a direct assign() call, by the CLI (a real subprocess against a real
board path), and by the queue-file trigger (TRIG-02) produce BYTE-IDENTICAL
values on the seven routing fields:

    title, body, workspace_kind, workspace_path,
    verify_command, verify_timeout, expected_artifacts

It also pins the queue trigger's idempotency contract: re-running a queue line
whose verify_command / title / prompt / timeout / artifacts changed must return
the SAME task id and refresh NONE of the columns — a full no-op, never a partial
update. A partial update would bolt a fresh oracle onto an old workspace and
report the mismatch as success, which is the exact hazard board.create_task
guards against.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from support import BoardTestCase  # noqa: E402

import assign  # noqa: E402
import board  # noqa: E402

ASSIGN_PY = Path(__file__).resolve().parents[1] / "src" / "assign.py"

TITLE = "shared task"
PROMPT = "make a file"
VERIFY = "python check.py"
TIMEOUT = 120
ARTIFACTS = ["out/r.json", "out/notes.md"]


class TheThreeTriggersWriteOneRowShape(BoardTestCase):
    ROUTING_FIELDS = (
        "title", "body", "workspace_kind", "workspace_path",
        "verify_command", "verify_timeout", "expected_artifacts",
    )

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.tmp / "workspace"
        self.repo.mkdir()
        (self.repo / "readme.md").write_text("# workspace\n", encoding="utf-8")

    def _routing(self, task_id: str) -> tuple[Any, ...]:
        row = self.task_row(task_id)
        return tuple(row[f] for f in self.ROUTING_FIELDS)

    # -- the three triggers ----------------------------------------------

    def _via_direct_assign(self) -> str:
        return assign.assign(
            self.conn,
            title=TITLE,
            prompt=PROMPT,
            verify_command=VERIFY,
            repo=self.repo,
            verify_timeout=TIMEOUT,
            expected_artifacts=ARTIFACTS,
        )

    def _via_cli(self) -> str:
        proc = subprocess.run(
            [sys.executable, str(ASSIGN_PY),
             "--repo", str(self.repo),
             "--prompt", PROMPT,
             "--verify", VERIFY,
             "--verify-timeout", str(TIMEOUT),
             "--title", TITLE,
             "--expected-artifacts", ARTIFACTS[0],
             "--expected-artifacts", ARTIFACTS[1],
             "--board", str(self.db_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=dict(os.environ, TRIAI_BOARD_DB=str(self.db_path),
                     HERMES_KANBAN_DB=str(self.db_path)),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        task_id = proc.stdout.strip().splitlines()[-1]
        self.assertEqual(self.task_row(task_id)["title"], TITLE)
        return task_id

    def _queue_file(self, **overrides) -> Path:
        line = {
            "id": "nightly-1",
            "title": TITLE,
            "repo": str(self.repo),
            "prompt": PROMPT,
            "verify": VERIFY,
            "verify_timeout": TIMEOUT,
            "expected_artifacts": ARTIFACTS,
            "timeout": 600,
            "branch": "main",
        }
        line.update(overrides)
        queue = self.tmp / "queue.jsonl"
        queue.write_text(json.dumps(line) + "\n", encoding="utf-8")
        return queue

    def _via_queue(self, **overrides) -> str:
        created = assign.assign_queue(self.conn, self._queue_file(**overrides))
        self.assertEqual(len(created), 1)
        return created[0][1]

    # -- identity --------------------------------------------------------

    def test_cli_and_direct_assign_are_byte_identical(self):
        direct_id = self._via_direct_assign()
        cli_id = self._via_cli()
        self.assertEqual(
            self._routing(direct_id), self._routing(cli_id),
            "CLI routing fields must be byte-identical to a direct assign()",
        )

    def test_queue_and_direct_assign_are_byte_identical(self):
        direct_id = self._via_direct_assign()
        queue_id = self._via_queue()
        self.assertEqual(
            self._routing(direct_id), self._routing(queue_id),
            "queue routing fields must be byte-identical to a direct assign()",
        )

    def test_all_three_triggers_converge_on_one_shape(self):
        direct = self._routing(self._via_direct_assign())
        cli = self._routing(self._via_cli())
        queue = self._routing(self._via_queue())
        self.assertEqual(direct, cli)
        self.assertEqual(direct, queue)
        # The row genuinely carries the seven fields, not just "equal to itself".
        self.assertEqual(direct[0], TITLE)                     # title
        self.assertEqual(direct[1], PROMPT)                    # body
        self.assertEqual(direct[2], "dir")                     # workspace_kind
        self.assertEqual(direct[3], str(self.repo))            # workspace_path
        self.assertEqual(direct[4], VERIFY)                    # verify_command
        self.assertEqual(direct[5], TIMEOUT)                   # verify_timeout
        self.assertEqual(json.loads(direct[6]), ARTIFACTS)     # expected_artifacts

    def test_the_three_tasks_are_three_distinct_rows(self):
        ids = {self._via_direct_assign(), self._via_cli(), self._via_queue()}
        self.assertEqual(len(ids), 3,
                         "three trigger invocations must create three distinct tasks")

    # -- the queue trigger's idempotency contract ------------------------

    def test_rerun_of_a_changed_queue_line_refreshes_nothing(self):
        first = self._via_queue()
        before = dict(self.task_row(first))

        # Same idempotency key; EVERYTHING else changes.
        self._via_queue(
            title="completely different",
            prompt="a totally different prompt",
            verify="python OTHER.py",
            verify_timeout=999,
            expected_artifacts=["other.txt"],
            repo=str(self.tmp / "elsewhere"),
            timeout=1,
        )

        after = dict(self.task_row(first))
        self.assertEqual(
            after, before,
            "re-running a queue line must NOT refresh any column on the existing "
            "row — idempotency is a no-op, never a partial update",
        )
        count = self.conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE idempotency_key = 'nightly-1'"
        ).fetchone()["c"]
        self.assertEqual(count, 1, "re-running a queue line must not create a duplicate")

    def test_rerun_returns_the_same_task_id(self):
        first = self._via_queue()
        self._via_queue(verify="python OTHER.py", title="changed")
        rows = self.conn.execute(
            "SELECT id FROM tasks WHERE idempotency_key = 'nightly-1'"
        ).fetchall()
        row_ids = [r["id"] for r in rows]
        self.assertEqual(row_ids, [first],
                         "the identical queue line must resolve to the existing task")


if __name__ == "__main__":
    unittest.main()