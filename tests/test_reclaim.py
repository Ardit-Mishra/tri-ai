"""Phase 1, criterion 4: a claim outlives its lease only as long as its worker
does. A dead worker's task becomes claimable again; a live worker's claim is
extended rather than released, so no second worker is ever spawned beside it."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import BoardTestCase  # noqa: E402

import board  # noqa: E402

SLEEPER = [sys.executable, "-c", "import time; time.sleep(300)"]


class ReclaimTestCase(BoardTestCase):
    def ready_task(self, title: str = "chore") -> str:
        return board.create_task(
            self.conn, title=title, prompt="a chore", verify_command="true"
        )

    def claim_with_worker(self, task_id: str, pid: int) -> str:
        """Claim ``task_id`` as a host-local worker running under ``pid``."""
        claimer = self.host_local_claimer(pid)
        claimed = self.kb.claim_task(self.conn, task_id, claimer=claimer)
        self.assertIsNotNone(claimed)
        self.kb._set_worker_pid(self.conn, task_id, pid)
        return claimer

    def expire_lease(self, task_id: str) -> None:
        """Move the claim's expiry into the past — the lease TTL has elapsed."""
        past = int(time.time()) - 60
        with self.kb.write_txn(self.conn):
            self.conn.execute(
                "UPDATE tasks SET claim_expires = ? WHERE id = ?", (past, task_id)
            )
            self.conn.execute(
                "UPDATE task_runs SET claim_expires = ? "
                "WHERE task_id = ? AND ended_at IS NULL",
                (past, task_id),
            )

    def spawn_sleeper(self) -> int:
        proc = subprocess.Popen(SLEEPER)
        self.addCleanup(self._kill, proc)
        deadline = time.time() + 10
        while time.time() < deadline and not self.kb._pid_alive(proc.pid):
            time.sleep(0.05)
        self.assertTrue(self.kb._pid_alive(proc.pid), "sleeper never came up")
        return proc.pid

    def dead_pid(self) -> int:
        proc = subprocess.Popen(SLEEPER)
        pid = proc.pid
        proc.kill()
        proc.wait(timeout=10)
        deadline = time.time() + 10
        while time.time() < deadline and self.kb._pid_alive(pid):
            time.sleep(0.05)
        self.assertFalse(self.kb._pid_alive(pid), "killed worker still looks alive")
        return pid

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass


class DeadWorkerIsReclaimed(ReclaimTestCase):
    def test_killed_worker_makes_the_task_claimable_again(self):
        tid = self.ready_task("killed mid-claim")
        self.claim_with_worker(tid, self.dead_pid())
        self.expire_lease(tid)

        self.assertEqual(board.release_stale_claims(self.conn), 1)

        row = self.task_row(tid)
        self.assertEqual(row["status"], "ready")
        self.assertIsNone(row["claim_lock"])
        self.assertIsNone(row["claim_expires"])
        self.assertIsNone(row["worker_pid"])
        self.assertIn("reclaimed", self.event_kinds(tid))

        # "Claimable again" has to mean an actual successful claim.
        second = self.kb.claim_task(
            self.conn, tid, claimer=self.host_local_claimer(os.getpid())
        )
        self.assertIsNotNone(second)

    def test_the_dead_workers_run_is_closed_not_left_in_flight(self):
        tid = self.ready_task("run bookkeeping")
        self.claim_with_worker(tid, self.dead_pid())
        self.expire_lease(tid)
        board.release_stale_claims(self.conn)

        run = self.conn.execute(
            "SELECT status, outcome, ended_at FROM task_runs WHERE task_id = ?",
            (tid,),
        ).fetchone()
        self.assertEqual(run["status"], "reclaimed")
        self.assertEqual(run["outcome"], "reclaimed")
        self.assertIsNotNone(run["ended_at"])

    def test_an_unexpired_claim_is_left_alone(self):
        tid = self.ready_task("still within lease")
        self.claim_with_worker(tid, self.dead_pid())
        # No expire_lease() — the lease has not elapsed yet.
        self.assertEqual(board.release_stale_claims(self.conn), 0)
        self.assertEqual(self.task_row(tid)["status"], "running")


class LiveWorkerDefersTheReclaim(ReclaimTestCase):
    def test_expired_lease_with_a_live_worker_extends_instead_of_releasing(self):
        pid = self.spawn_sleeper()
        tid = self.ready_task("slow but alive")
        claimer = self.claim_with_worker(tid, pid)
        self.expire_lease(tid)
        expired_at = self.task_row(tid)["claim_expires"]

        signalled: list[tuple[int, int]] = []

        def recording_signal(target_pid, sig):
            signalled.append((target_pid, sig))
            return board.posix_semantics_signal(target_pid, sig)

        self.assertEqual(
            board.release_stale_claims(self.conn, signal_fn=recording_signal), 0
        )

        row = self.task_row(tid)
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["claim_lock"], claimer)
        self.assertEqual(row["worker_pid"], pid)
        self.assertGreater(row["claim_expires"], expired_at)
        self.assertIn("claim_extended", self.event_kinds(tid))
        self.assertEqual(signalled, [], "a live worker must not be signalled")
        self.assertTrue(self.kb._pid_alive(pid), "the live worker was killed")

    def test_the_task_stays_unclaimable_while_its_worker_lives(self):
        pid = self.spawn_sleeper()
        tid = self.ready_task("no double spawn")
        self.claim_with_worker(tid, pid)
        self.expire_lease(tid)
        board.release_stale_claims(self.conn)

        # This is the double-spawn the deferral exists to prevent.
        self.assertIsNone(
            self.kb.claim_task(
                self.conn, tid, claimer=self.host_local_claimer(os.getpid())
            )
        )
        runs = self.conn.execute(
            "SELECT COUNT(*) c FROM task_runs WHERE task_id = ? AND ended_at IS NULL",
            (tid,),
        ).fetchone()["c"]
        self.assertEqual(runs, 1)


class PosixSemanticsSignal(ReclaimTestCase):
    """The kernel reads ``ProcessLookupError`` as "already terminated". Windows'
    ``os.kill`` raises ``PermissionError``/``OSError`` for a dead PID instead,
    which the kernel reads as "still alive" — deferring every reclaim forever.
    This adapter restores the semantics the kernel is written against."""

    def test_dead_pid_reports_process_lookup_error(self):
        with self.assertRaises(ProcessLookupError):
            board.posix_semantics_signal(self.dead_pid(), signal.SIGTERM)

    def test_never_existing_pid_reports_process_lookup_error(self):
        with self.assertRaises(ProcessLookupError):
            board.posix_semantics_signal(999_999, signal.SIGTERM)

    def test_live_pid_is_actually_signalled(self):
        pid = self.spawn_sleeper()
        board.posix_semantics_signal(pid, signal.SIGTERM)
        deadline = time.time() + 10
        while time.time() < deadline and self.kb._pid_alive(pid):
            time.sleep(0.05)
        self.assertFalse(self.kb._pid_alive(pid))


if __name__ == "__main__":
    unittest.main()
