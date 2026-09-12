"""Proofs that the polling daemon does not invent a second worker lifecycle."""

from __future__ import annotations

import sys
import signal
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import worker_daemon  # noqa: E402


class Attempt:
    def __init__(self, *, stop: bool = False) -> None:
        self.stop = stop


class WorkerDaemonLoop(unittest.TestCase):
    def test_empty_queue_uses_capped_backoff(self):
        sleeps = []
        summary = worker_daemon.serve(
            lambda: None, sleep=sleeps.append, stop_requested=lambda: False,
            idle_min_seconds=1, idle_max_seconds=4, max_ticks=5,
        )
        self.assertEqual(sleeps, [1.0, 2.0, 4.0, 4.0])
        self.assertEqual((summary.ticks, summary.idle_sleeps), (5, 4))

    def test_a_real_attempt_resets_idle_delay_without_creating_a_retry(self):
        values = iter([None, None, Attempt(), None])
        sleeps = []
        summary = worker_daemon.serve(
            lambda: next(values), sleep=sleeps.append, stop_requested=lambda: False,
            idle_min_seconds=1, idle_max_seconds=8, max_ticks=4,
        )
        self.assertEqual(sleeps, [1.0, 2.0])
        self.assertEqual(summary.ticks, 4)

    def test_stop_requested_after_idle_sleep_prevents_another_claim_tick(self):
        sleeps = []
        summary = worker_daemon.serve(
            lambda: None,
            sleep=lambda delay: sleeps.append(delay),
            stop_requested=lambda: bool(sleeps),
        )
        self.assertEqual((summary.ticks, summary.idle_sleeps), (1, 1))
        self.assertTrue(summary.stopped)

    def test_an_attempt_that_requests_a_hard_stop_ends_the_daemon(self):
        summary = worker_daemon.serve(
            lambda: Attempt(stop=True), sleep=lambda _delay: self.fail("must not sleep"),
            stop_requested=lambda: False,
        )
        self.assertEqual((summary.ticks, summary.idle_sleeps), (1, 0))
        self.assertTrue(summary.stopped)

    def test_invalid_backoff_configuration_is_refused(self):
        with self.assertRaises(ValueError):
            worker_daemon.serve(lambda: None, sleep=lambda _: None, stop_requested=lambda: False,
                                idle_min_seconds=0)

    def test_one_daemon_tick_reaches_the_existing_lease_reclaim_path(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "board.db"
            with mock.patch.object(
                worker_daemon.board,
                "release_stale_claims",
                wraps=worker_daemon.board.release_stale_claims,
            ) as release:
                self.assertEqual(worker_daemon.main(["--board", str(database), "--once"]), 0)
            release.assert_called_once()

    def test_main_logs_the_actual_worker_failure_reason(self):
        stderr = StringIO()
        with mock.patch.object(worker_daemon.board, "connect", side_effect=ValueError("bad board path")), redirect_stderr(stderr):
            result = worker_daemon.main(["--once"])

        self.assertEqual(result, worker_daemon.worker.EXIT_ERROR)
        self.assertEqual(
            stderr.getvalue(), "worker daemon stopped: ValueError: bad board path\n",
        )

    def test_windows_break_signal_is_registered_as_a_clean_stop_request(self):
        if not hasattr(signal, "SIGBREAK"):
            self.skipTest("SIGBREAK is Windows-only")
        seen = []
        with mock.patch.object(worker_daemon.signal, "signal", side_effect=lambda sig, handler: seen.append(sig)):
            with tempfile.TemporaryDirectory() as temp:
                result = worker_daemon.main(["--board", str(Path(temp) / "board.db"), "--once"])
        self.assertEqual(result, 0)
        self.assertIn(signal.SIGBREAK, seen)


if __name__ == "__main__":
    unittest.main()
