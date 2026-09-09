"""Phase 1, criterion 3: concurrent claimants on one ready task — exactly one
wins, every other observes the task already taken."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import BoardTestCase  # noqa: E402

import board  # noqa: E402

CHILD = Path(__file__).resolve().parent / "_claim_child.py"


class ConcurrentClaimIsExclusive(BoardTestCase):
    def race(self, task_id: str, claimants: int) -> list[dict]:
        # Give the children time to boot and import before the deadline; the
        # point of the barrier is that they all reach claim_task together.
        start_at = time.time() + 6.0
        procs = []
        for _ in range(claimants):
            proc = subprocess.Popen(
                [sys.executable, str(CHILD), str(self.db_path), task_id, f"{start_at}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            procs.append(proc)
            # Registered at spawn time, not after the wait loop: if an assertion
            # below fires on the first child, or communicate() times out, the
            # remaining children are never waited on. They would outlive the
            # test still holding a claim against a board the next test reuses,
            # so one failure would cascade into unrelated ones.
            self.addCleanup(self._reap, proc)
        results = []
        for p in procs:
            out, err = p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0, f"claimant crashed: {err}")
            results.append(json.loads(out))
        for r in results:
            self.assertNotIn("error", r, f"claimant raised: {r.get('error')}")
        return results

    @staticmethod
    def _reap(proc: subprocess.Popen) -> None:
        """Make sure a claimant is dead. A no-op for one that already exited."""
        if proc.poll() is not None:
            return
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass

    def make_ready_task(self, title: str) -> str:
        tid = board.create_task(
            self.conn,
            title=title,
            prompt="a chore",
            verify_command="true",
        )
        self.assertEqual(self.task_row(tid)["status"], "ready")
        return tid

    def assert_exactly_one_winner(self, task_id: str, results: list[dict]) -> None:
        winners = [r for r in results if r["claimed"]]
        losers = [r for r in results if not r["claimed"]]
        self.assertEqual(
            len(winners), 1, f"expected exactly one claim to succeed, got {results}"
        )
        self.assertEqual(len(losers), len(results) - 1)

        row = self.task_row(task_id)
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["claim_lock"], winners[0]["claimer"])
        # One claim means one run row, not one per claimant.
        runs = self.conn.execute(
            "SELECT COUNT(*) c FROM task_runs WHERE task_id = ?", (task_id,)
        ).fetchone()["c"]
        self.assertEqual(runs, 1)

    def test_two_claimants_one_winner(self):
        tid = self.make_ready_task("two-way race")
        self.assert_exactly_one_winner(tid, self.race(tid, 2))

    def test_four_claimants_one_winner(self):
        tid = self.make_ready_task("four-way race")
        self.assert_exactly_one_winner(tid, self.race(tid, 4))

    def test_a_second_claim_after_the_first_is_refused(self):
        tid = self.make_ready_task("sequential")
        first = self.kb.claim_task(self.conn, tid, claimer=self.host_local_claimer(1))
        second = self.kb.claim_task(self.conn, tid, claimer=self.host_local_claimer(2))
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_a_task_gated_by_an_undone_parent_cannot_be_claimed(self):
        parent = self.make_ready_task("parent")
        child = board.create_task(
            self.conn,
            title="child",
            prompt="a chore",
            verify_command="true",
            parents=[parent],
        )
        self.assertIsNone(
            self.kb.claim_task(self.conn, child, claimer=self.host_local_claimer(3))
        )


if __name__ == "__main__":
    unittest.main()
