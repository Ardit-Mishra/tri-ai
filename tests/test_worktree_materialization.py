"""Phase 4 Slice 2: materialize owned worktrees before concurrent dispatch."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import board  # noqa: E402
import dispatcher  # noqa: E402
import executor  # noqa: E402
import worker  # noqa: E402
import worktrees  # noqa: E402
from dispatcher import WorkerResult  # noqa: E402
from support import BoardTestCase  # noqa: E402
from test_phase3_concurrency import _write_concurrency  # noqa: E402


def _init_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Tri-AI Test"], cwd=path, check=True)
    (path / "sentinel.txt").write_text("source remains intact\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=path, check=True)


class WorktreeMaterializationTest(BoardTestCase):
    def _source_repo(self) -> Path:
        repo = self.tmp / "source"
        _init_repo(repo)
        return repo

    def _worktree_task(self, source: Path, branch: str) -> str:
        return board.create_task(
            self.conn,
            title=f"worktree {branch}",
            prompt="edit only this isolated checkout",
            verify_command="echo ok",
            verify_timeout=30,
            workspace_kind="worktree",
            workspace_path=source,
            branch_name=branch,
        )

    def test_same_repo_siblings_materialize_before_overlapping_launch(self):
        source = self._source_repo()
        first = self._worktree_task(source, "triai/first")
        second = self._worktree_task(source, "triai/second")
        cap = self.tmp / "concurrency.json"
        _write_concurrency(cap, cap=2)
        self.conn.close()

        starts: dict[str, float] = {}
        lock = threading.Lock()

        def launcher(task_id: str) -> WorkerResult:
            conn = board.connect(self.db_path)
            try:
                rows = {
                    row["id"]: row
                    for row in conn.execute(
                        "SELECT id, workspace_path, branch_name FROM tasks "
                        "WHERE id IN (?, ?)",
                        (first, second),
                    ).fetchall()
                }
                first_path = Path(rows[first]["workspace_path"])
                second_path = Path(rows[second]["workspace_path"])
                self.assertNotEqual(first_path, second_path)
                self.assertNotEqual(rows[first]["branch_name"], rows[second]["branch_name"])
                self.assertTrue((first_path / ".git").exists())
                self.assertTrue((second_path / ".git").exists())
                first_branch = subprocess.run(
                    ["git", "branch", "--show-current"], cwd=first_path,
                    capture_output=True, text=True, check=True,
                ).stdout.strip()
                second_branch = subprocess.run(
                    ["git", "branch", "--show-current"], cwd=second_path,
                    capture_output=True, text=True, check=True,
                ).stdout.strip()
                self.assertEqual(first_branch, rows[first]["branch_name"])
                self.assertEqual(second_branch, rows[second]["branch_name"])
                with lock:
                    starts[task_id] = time.monotonic()
                time.sleep(0.12)
                board.kanban().complete_task(conn, task_id, result="mock", summary="done")
            finally:
                conn.close()
            return WorkerResult(task_id=task_id, exit_code=0, pid=100 + len(starts))

        result = dispatcher.dispatch(
            launcher=launcher,
            board_path=self.db_path,
            concurrency_source=cap,
            max_waves=1,
        )

        self.assertTrue(result.all_passed, result.skipped)
        self.assertEqual(set(starts), {first, second})
        self.assertLess(abs(starts[first] - starts[second]), 0.10)
        self.assertEqual((source / "sentinel.txt").read_text(encoding="utf-8"), "source remains intact\n")
        self.assertEqual(len(board.list_worktrees(board.connect(self.db_path))), 2)

    def test_materialization_failure_skips_without_claim_or_fallback_to_source(self):
        source = self._source_repo()
        task_id = self._worktree_task(source, "triai/unavailable")
        cap = self.tmp / "concurrency.json"
        _write_concurrency(cap, cap=1)
        self.conn.close()
        invoked: list[str] = []

        def launcher(task_id: str) -> WorkerResult:
            invoked.append(task_id)
            return WorkerResult(task_id=task_id, exit_code=0)

        with mock.patch.object(
            worktrees.executor,
            "materialize_worktree",
            side_effect=executor.WorktreeError("deliberate create failure"),
        ):
            result = dispatcher.dispatch(
                launcher=launcher,
                board_path=self.db_path,
                concurrency_source=cap,
                max_waves=1,
            )

        check = board.connect(self.db_path)
        try:
            row = check.execute(
                "SELECT status, claim_lock, workspace_path FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        finally:
            check.close()
        self.assertEqual(invoked, [])
        self.assertEqual(result.results, [])
        self.assertFalse(result.all_passed)
        self.assertEqual(result.skipped[0]["task_id"], task_id)
        self.assertEqual(row["status"], "ready")
        self.assertIsNone(row["claim_lock"])
        self.assertEqual(Path(row["workspace_path"]), source)
        self.assertTrue((source / "sentinel.txt").exists())

    def test_dirty_source_skips_before_the_worktree_gateway_runs(self):
        source = self._source_repo()
        (source / "untracked.txt").write_text("do not snapshot this\n", encoding="utf-8")
        task_id = self._worktree_task(source, "triai/dirty-source")
        cap = self.tmp / "concurrency.json"
        _write_concurrency(cap, cap=1)
        self.conn.close()

        with mock.patch.object(worktrees.executor, "materialize_worktree") as create:
            result = dispatcher.dispatch(
                launcher=lambda tid: WorkerResult(task_id=tid, exit_code=0),
                board_path=self.db_path,
                concurrency_source=cap,
                max_waves=1,
            )

        check = board.connect(self.db_path)
        try:
            row = check.execute(
                "SELECT status, claim_lock, workspace_path FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        finally:
            check.close()
        create.assert_not_called()
        self.assertEqual(result.results, [])
        self.assertFalse(result.all_passed)
        self.assertIn("source is not a clean", result.skipped[0]["reason"])
        self.assertEqual(row["status"], "ready")
        self.assertIsNone(row["claim_lock"])
        self.assertEqual(Path(row["workspace_path"]), source)

    def test_named_worker_materializes_before_claiming(self):
        source = self._source_repo()
        task_id = self._worktree_task(source, "triai/named-worker")
        ledger_path = self.tmp / "ledger.jsonl"
        runs_root = self.tmp / "runs"

        with mock.patch.object(
            executor,
            "run_agent",
            return_value=executor.AgentResult(0, "agent\n", 0.01),
        ), mock.patch.object(
            executor,
            "run_verify",
            return_value=executor.VerifyResult("passed", 0, "verify\n", 0.01),
        ):
            attempt = worker.run_once(
                self.conn,
                task_id=task_id,
                ledger_path=ledger_path,
                runs_root=runs_root,
            )

        row = self.task_row(task_id)
        self.assertIsNotNone(attempt)
        self.assertEqual(attempt.outcome, "passed")
        self.assertEqual(row["status"], "done")
        self.assertNotEqual(Path(row["workspace_path"]), source)
        self.assertTrue((Path(row["workspace_path"]) / ".git").exists())

    def test_dispatcher_worker_argv_carries_the_selected_task_id(self):
        argv = dispatcher._worker_argv(
            "task-123",
            board_path=self.db_path,
            ledger_path=self.tmp / "ledger.jsonl",
            runs_root=self.tmp / "runs",
        )
        self.assertIn("--task-id", argv)
        self.assertEqual(argv[argv.index("--task-id") + 1], "task-123")


if __name__ == "__main__":
    unittest.main()
