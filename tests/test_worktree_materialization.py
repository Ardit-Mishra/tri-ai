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
        return repo.resolve()

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

    def test_preexisting_deterministic_target_adopts_only_for_exact_source_and_branch(self):
        source = self._source_repo()
        task_id = self._worktree_task(source, "triai/adopt-crash-target")
        target = worktrees._target_path(source, task_id)
        subprocess.run(
            ["git", "worktree", "add", "-qb", "triai/adopt-crash-target", str(target)],
            cwd=source,
            check=True,
        )

        task = dict(self.task_row(task_id))
        resolution = worktrees.resolve_task(self.conn, task)

        self.assertTrue(resolution.ready, resolution.reason)
        self.assertEqual(Path(resolution.task["workspace_path"]), target)
        record = board.worktree_for_task(self.conn, task_id)
        self.assertIsNotNone(record)
        self.assertEqual(Path(record["source_path"]), source)
        self.assertEqual(Path(record["target_path"]), target)
        self.assertEqual(record["branch_name"], "triai/adopt-crash-target")

        wrong_source = self._worktree_task(source, "triai/wrong-source")
        wrong_source_target = worktrees._target_path(source, wrong_source)
        wrong_source_target.parent.mkdir(parents=True, exist_ok=True)
        foreign_source = self.tmp / "foreign-source"
        _init_repo(foreign_source)
        subprocess.run(
            ["git", "worktree", "add", "-qb", "triai/wrong-source", str(wrong_source_target)],
            cwd=foreign_source,
            check=True,
        )
        wrong_source_resolution = worktrees.resolve_task(
            self.conn, dict(self.task_row(wrong_source))
        )
        self.assertFalse(wrong_source_resolution.ready)
        self.assertIn("existing worktree target cannot be proven owned", wrong_source_resolution.reason)
        self.assertIsNone(board.worktree_for_task(self.conn, wrong_source))
        wrong_source_row = self.task_row(wrong_source)
        self.assertEqual(wrong_source_row["status"], "ready")
        self.assertIsNone(wrong_source_row["claim_lock"])
        self.assertTrue(wrong_source_target.exists(), "rejected worktree remains retained evidence")

        wrong_branch = self._worktree_task(source, "triai/expected-existing-branch")
        wrong_branch_target = worktrees._target_path(source, wrong_branch)
        subprocess.run(
            ["git", "worktree", "add", "-qb", "triai/other-existing-branch", str(wrong_branch_target)],
            cwd=source,
            check=True,
        )
        wrong_branch_resolution = worktrees.resolve_task(
            self.conn, dict(self.task_row(wrong_branch))
        )
        self.assertFalse(wrong_branch_resolution.ready)
        self.assertIn("existing worktree target cannot be proven owned", wrong_branch_resolution.reason)
        self.assertIsNone(board.worktree_for_task(self.conn, wrong_branch))
        wrong_branch_row = self.task_row(wrong_branch)
        self.assertEqual(wrong_branch_row["status"], "ready")
        self.assertIsNone(wrong_branch_row["claim_lock"])
        self.assertTrue(wrong_branch_target.exists(), "rejected worktree remains retained evidence")

    def test_wrong_recorded_branch_skips_before_dispatch_claims(self):
        source = self._source_repo()
        task_id = self._worktree_task(source, "triai/expected-branch")
        target = worktrees._target_path(source, task_id)
        subprocess.run(
            ["git", "worktree", "add", "-qb", "triai/wrong-branch", str(target)],
            cwd=source,
            check=True,
        )
        board.record_worktree(
            self.conn,
            task_id=task_id,
            source_path=source,
            target_path=target,
            branch_name="triai/expected-branch",
        )
        cap = self.tmp / "concurrency.json"
        _write_concurrency(cap, cap=1)
        self.conn.close()
        invoked: list[str] = []

        result = dispatcher.dispatch(
            launcher=lambda tid: invoked.append(tid) or WorkerResult(task_id=tid, exit_code=0),
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
        self.assertIn("recorded worktree ownership", result.skipped[0]["reason"])
        self.assertEqual(row["status"], "ready")
        self.assertIsNone(row["claim_lock"])
        self.assertEqual(Path(row["workspace_path"]), target)
        self.assertTrue(target.exists(), "rejected worktree remains retained evidence")

    def test_named_worker_materializes_before_claiming(self):
        # What this test is about is in its name: a worker invoked with an
        # explicit --task-id must materialize the worktree before it claims,
        # exactly as the selection path does. The dispatcher spawns every
        # worker that way, so a gap here would be the normal case.
        #
        # It used to assert that by looking for `.git` in the workspace AFTER
        # the run, and failed - not because materialization was skipped, but
        # because the KERNEL removes a task-owned worktree when the task
        # completes (kanban_db.complete_task -> _cleanup_workspace ->
        # `git worktree remove`). Tri-AI's worktrees.py says it never removes
        # one, and that is true of the module and misleading about the system.
        # The removal is guarded - see the test below - but the workspace is
        # legitimately gone by the time the run is over, so the evidence has to
        # be taken while it exists.
        source = self._source_repo()
        task_id = self._worktree_task(source, "triai/named-worker")
        observed: dict[str, object] = {}
        materialize = worktrees.resolve_task

        def watched(conn, task):
            resolution = materialize(conn, task)
            if resolution.ready:
                path = Path(resolution.task["workspace_path"])
                observed["path"] = path
                observed["is_checkout"] = (path / ".git").exists()
            return resolution

        with mock.patch.object(worktrees, "resolve_task", watched), \
            mock.patch.object(
                executor, "run_agent",
                return_value=executor.AgentResult(0, "agent\n", 0.01),
            ), mock.patch.object(
                executor, "run_verify",
                return_value=executor.VerifyResult("passed", 0, "verify\n", 0.01),
            ):
            attempt = worker.run_once(
                self.conn,
                task_id=task_id,
                ledger_path=self.tmp / "ledger.jsonl",
                runs_root=self.tmp / "runs",
            )

        row = self.task_row(task_id)
        self.assertIsNotNone(attempt)
        self.assertEqual(attempt.outcome, "passed")
        self.assertEqual(row["status"], "done")
        self.assertNotEqual(Path(row["workspace_path"]), source)

        self.assertTrue(
            observed.get("is_checkout"),
            "the worktree was not a real checkout when the claim was taken",
        )
        self.assertEqual(Path(row["workspace_path"]), observed.get("path"))
        self.assertIsNotNone(
            board.worktree_for_task(self.conn, task_id),
            "ownership was never recorded, so a later run could not adopt it",
        )

    def test_work_left_in_a_worktree_survives_the_kernels_cleanup(self):
        # The property that actually protects a deliverable. The kernel removes
        # a completed task's worktree, but only when git says it is clean and
        # carries nothing unpushed - no --force, and any doubt preserves it. A
        # run that leaves real files behind must therefore still have them.
        source = self._source_repo()
        task_id = self._worktree_task(source, "triai/dirty-worktree")
        left_behind: dict[str, Path] = {}

        def agent_that_builds(repo, prompt, **kwargs):
            deliverable = Path(repo) / "index.html"
            deliverable.write_text("<h1>built</h1>", encoding="utf-8")
            left_behind["path"] = deliverable
            return executor.AgentResult(0, "agent\n", 0.01)

        with mock.patch.object(executor, "run_agent", agent_that_builds), \
            mock.patch.object(
                executor, "run_verify",
                return_value=executor.VerifyResult("passed", 0, "verify\n", 0.01),
            ):
            attempt = worker.run_once(
                self.conn,
                task_id=task_id,
                ledger_path=self.tmp / "ledger.jsonl",
                runs_root=self.tmp / "runs",
            )

        self.assertIsNotNone(attempt)
        self.assertEqual(attempt.outcome, "passed")
        self.assertIn("path", left_behind, "the stand-in agent never wrote")
        self.assertTrue(
            left_behind["path"].exists(),
            "the kernel removed a worktree that still held this run's work",
        )

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
