"""An agent's leftovers must not deadlock the workspace it ran in.

Measured on the desktop. `t_244fe6f7`'s agent wrote BRIEF.md and four
`src/*.js` files; its worker was killed by a daemon restart; the claim was
released and the task cancelled. The files stayed. Every task claimed
afterwards in that workspace then hit the clean-tree precheck and skipped -
for ever. Two specialists from the first real fan-out died that way without
writing a byte.

The precheck is right and stays: *"the worker NEVER touches a repo it did not
dirty."* It cannot tell an orphan's leftovers from the operator's own edits,
so it refuses both. What was missing is the other half - nobody ever cleaned
up after the orphan, and nothing in the system knew it should.

This is the narrow case where Tri-AI does know the dirt is its own: a task it
claimed, in a workspace it recorded, whose worker is gone. That, and only
that, is reverted - through `executor.revert`, which stashes and never
deletes, so the work is recoverable with `git stash pop` exactly as a failed
run's is.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import board  # noqa: E402
import executor  # noqa: E402
import worker  # noqa: E402
from support import BoardTestCase  # noqa: E402


class OrphanRecoveryTest(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.tmp / "workspace"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"],
                       cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=self.repo, check=True)
        (self.repo / "README.md").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.repo, check=True)

    def dirty(self) -> list[str]:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=self.repo, capture_output=True, text=True).stdout
        return [line for line in out.splitlines() if line.strip()]

    def orphan(self, *, pid: int = 999999) -> str:
        """A task left running by a worker that is no longer alive."""
        task_id = board.create_task(
            self.conn, title="orphaned", prompt="build", repo=self.repo,
            verify_command="python verify.py", verify_timeout=30,
        )
        self.assertIsNotNone(
            self.kb.claim_task(self.conn, task_id, claimer="dead-worker"))
        self.conn.execute(
            "UPDATE tasks SET worker_pid = ?, claim_expires = 1 WHERE id = ?",
            (pid, task_id))
        self.conn.commit()
        (self.repo / "half-built.html").write_text("<h1>wip</h1>", encoding="utf-8")
        return task_id

    # -- the recovery -----------------------------------------------------

    def test_a_dead_workers_leftovers_are_stashed_not_left_to_block(self):
        self.orphan()
        self.assertTrue(self.dirty(), "fixture should start dirty")
        worker.recover_orphans(self.conn)
        self.assertEqual(self.dirty(), [],
                         "the workspace is still blocked for every later task")

    def test_the_work_is_recoverable_rather_than_deleted(self):
        """Same contract as a failed run: stash, never delete."""
        self.orphan()
        worker.recover_orphans(self.conn)
        stashes = subprocess.run(["git", "stash", "list"], cwd=self.repo,
                                 capture_output=True, text=True).stdout
        self.assertIn("triai-revert", stashes)

    def test_the_task_returns_to_ready_so_it_can_be_retried(self):
        task_id = self.orphan()
        worker.recover_orphans(self.conn)
        self.assertIn(self.task_row(task_id)["status"], {"ready", "todo"})

    def test_it_reports_what_it_recovered(self):
        self.orphan()
        self.assertEqual(worker.recover_orphans(self.conn), 1)

    # -- the refusals -----------------------------------------------------

    def test_a_live_worker_is_never_touched(self):
        """Reverting under a running agent would destroy work in progress."""
        import os
        self.orphan(pid=os.getpid())     # this process is alive by definition
        self.assertEqual(worker.recover_orphans(self.conn), 0)
        self.assertTrue(self.dirty(), "a live claim's workspace must be left alone")

    def test_a_clean_workspace_is_left_entirely_alone(self):
        task_id = self.orphan()
        subprocess.run(["git", "clean", "-fdq"], cwd=self.repo, check=True)
        self.assertEqual(self.dirty(), [])
        worker.recover_orphans(self.conn)
        stashes = subprocess.run(["git", "stash", "list"], cwd=self.repo,
                                 capture_output=True, text=True).stdout
        self.assertEqual(stashes.strip(), "", "nothing to stash, so no stash")
        self.assertIn(self.task_row(task_id)["status"], {"ready", "todo"})

    def test_a_workspace_no_task_recorded_is_not_swept(self):
        """Only a repo Tri-AI's own agent dirtied. Never a bare directory."""
        stray = self.tmp / "not-a-task"
        stray.mkdir()
        (stray / "loose.txt").write_text("x", encoding="utf-8")
        worker.recover_orphans(self.conn)
        self.assertTrue((stray / "loose.txt").exists())

    def test_a_failed_revert_does_not_stop_the_sweep(self):
        """One unrecoverable repo must not strand every other workspace."""
        self.orphan()
        with mock.patch.object(
                executor, "revert",
                return_value=executor.RevertResult("failed", None, "boom")):
            self.assertEqual(worker.recover_orphans(self.conn), 0)
        # And the claim is not released on a workspace still holding the work,
        # because a `ready` task there would skip on the next tick anyway.
        self.assertTrue(self.dirty())


class TickIntegrationTest(OrphanRecoveryTest):
    def test_the_sweep_runs_before_a_claim_is_attempted(self):
        """A tick that claims first would skip on the dirt it was about to
        clear, burning the attempt and returning the task to ready."""
        source = (Path(__file__).resolve().parents[1] / "src" / "worker.py"
                  ).read_text(encoding="utf-8")
        body = source.split("def run_once")[1][:1800]
        self.assertIn("recover_orphans", body)
        self.assertLess(body.index("recover_orphans"), body.index("claim_task"))


if __name__ == "__main__":
    unittest.main()
