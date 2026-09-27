"""Proofs for daemon restart supervision and the shared Windows liveness probe.

Both subjects exist because of one incident. On 2026-09-12 the Telegram child
lost an HTTPS request and exited 1; ``supervise`` returned on the first child
exit, so the worker was stopped underneath a run it had already claimed, and
the board was left holding a stranded claim against a dead PID. Separately,
the supervisor's own "is another supervisor running" guard used
``os.kill(pid, 0)``, which on Windows reports an exited process as alive and
raises an uncaught ``OSError`` for a PID that never existed.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import daemon_supervisor  # noqa: E402
import process_liveness  # noqa: E402


class FakeProcess:
    """A child whose exit the test controls, with no real process involved."""

    def __init__(self, pid: int, command: tuple[str, ...] = ()) -> None:
        self.pid = pid
        self.command = command
        self.returncode = None
        self.signals = []

    def poll(self):
        return self.returncode

    def send_signal(self, value):
        self.signals.append(value)
        self.returncode = 0

    def terminate(self):
        self.returncode = 1

    def wait(self, timeout=None):
        return self.returncode


STOP_SIGNAL = (
    daemon_supervisor.signal.CTRL_BREAK_EVENT
    if os.name == "nt"
    else daemon_supervisor.signal.SIGTERM
)


class RestartSupervisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.commands = daemon_supervisor.commands(
            root=self.root,
            board_path=self.root / "board.db",
            ledger_path=self.root / "ledger.jsonl",
            runs_root=self.root / "runs",
            intake_policy=None,
        )
        self.stop = self.root / "stop.request"
        self.spawned = []
        self.now = {"value": 0.0}

    def _popen(self, base_pid):
        def popen(command, **kwargs):
            process = FakeProcess(base_pid + len(self.spawned), tuple(command))
            self.spawned.append(process)
            return process

        return popen

    def _monotonic(self) -> float:
        return self.now["value"]

    def _drive(self, on_tick):
        """Replace time.sleep so backoff is advanced, not waited out."""
        original = daemon_supervisor.time.sleep
        self.addCleanup(setattr, daemon_supervisor.time, "sleep", original)
        daemon_supervisor.time.sleep = on_tick

    def _is_telegram(self, process) -> bool:
        return "telegram_daemon.py" in process.command[1]

    def test_a_telegram_child_that_exits_is_restarted_and_the_worker_survives(self):
        """The exact incident: one child dies, the other must keep running."""
        ticks = {"n": 0}

        def tick(_seconds):
            ticks["n"] += 1
            self.now["value"] += 1.0
            if ticks["n"] == 1:
                self.spawned[1].returncode = 1  # telegram loses its HTTPS request
            if ticks["n"] >= 12:
                self.stop.touch()

        self._drive(tick)
        changes = []
        result = daemon_supervisor.supervise(
            self.commands,
            log_dir=self.root / "logs",
            run_id="test",
            stop_path=self.stop,
            popen=self._popen(200),
            monotonic=self._monotonic,
            on_change=changes.append,
            policy=daemon_supervisor.RestartPolicy(base_seconds=2.0, max_restarts=8),
        )

        self.assertEqual(result, 0)
        self.assertEqual(len(self.spawned), 3, "worker, dead telegram, replacement telegram")
        self.assertTrue(self._is_telegram(self.spawned[2]))
        # The worker was never signalled before the operator's stop request.
        self.assertEqual(self.spawned[0].signals, [STOP_SIGNAL])
        # PIDs are republished by name: a positional reader would have
        # attributed the surviving worker's PID to the restarting daemon.
        self.assertTrue(changes)
        self.assertEqual(changes[-1], {"worker": 200, "telegram": 202})
        log = (self.root / "logs" / "telegram.log").read_text(encoding="utf-8")
        self.assertIn("telegram exited 1; restarting in 2s", log)

    def test_a_restarting_child_reports_no_pid_rather_than_a_stale_one(self):
        ticks = {"n": 0}

        def tick(_seconds):
            ticks["n"] += 1
            if ticks["n"] == 1:
                self.spawned[1].returncode = 1
            if ticks["n"] >= 4:
                self.stop.touch()
            # Never advance the clock: the child stays inside its backoff.

        self._drive(tick)
        changes = []
        daemon_supervisor.supervise(
            self.commands,
            log_dir=self.root / "logs",
            run_id="test",
            stop_path=self.stop,
            popen=self._popen(500),
            monotonic=self._monotonic,
            on_change=changes.append,
            policy=daemon_supervisor.RestartPolicy(base_seconds=30.0),
        )

        self.assertEqual(changes[0], {"worker": 500, "telegram": None})

    def test_a_child_failing_past_its_budget_stops_the_fleet_instead_of_spinning(self):
        def popen(command, **kwargs):
            process = FakeProcess(300 + len(self.spawned), tuple(command))
            if "telegram_daemon.py" in command[1]:
                process.returncode = 1  # an unfixable config fails identically every time
            self.spawned.append(process)
            return process

        def tick(_seconds):
            self.now["value"] += 120.0  # skip past every backoff window

        self._drive(tick)
        result = daemon_supervisor.supervise(
            self.commands,
            log_dir=self.root / "logs",
            run_id="test",
            stop_path=self.stop,
            popen=popen,
            monotonic=self._monotonic,
            policy=daemon_supervisor.RestartPolicy(base_seconds=1.0, max_restarts=3),
        )

        self.assertEqual(result, 1)
        telegram = [p for p in self.spawned if self._is_telegram(p)]
        self.assertEqual(len(telegram), 4, "initial launch plus exactly 3 restarts")
        log = (self.root / "logs" / "telegram.log").read_text(encoding="utf-8")
        self.assertIn("exhausts the budget", log)
        self.assertFalse(
            self.stop.exists(), "giving up must not fabricate an operator stop request",
        )

    def test_backoff_doubles_and_is_capped(self):
        policy = daemon_supervisor.RestartPolicy(base_seconds=2.0, max_seconds=30.0)
        self.assertEqual(
            [policy.delay_for(n) for n in range(1, 7)],
            [2.0, 4.0, 8.0, 16.0, 30.0, 30.0],
        )
        self.assertEqual(policy.delay_for(0), 0.0)

    def test_a_child_that_stays_up_has_its_backoff_reset(self):
        ticks = {"n": 0}

        def tick(_seconds):
            ticks["n"] += 1
            self.now["value"] += 60.0
            if ticks["n"] == 1:
                self.spawned[1].returncode = 1
            if ticks["n"] >= 8:
                self.stop.touch()

        self._drive(tick)
        daemon_supervisor.supervise(
            self.commands,
            log_dir=self.root / "logs",
            run_id="test",
            stop_path=self.stop,
            popen=self._popen(400),
            monotonic=self._monotonic,
            policy=daemon_supervisor.RestartPolicy(healthy_seconds=100.0),
        )
        log = (self.root / "logs" / "telegram.log").read_text(encoding="utf-8")
        self.assertIn("backoff reset", log)

    def test_a_stop_request_still_shuts_both_children_down_cleanly(self):
        self.stop.touch()
        result = daemon_supervisor.supervise(
            self.commands,
            log_dir=self.root / "logs",
            run_id="test",
            stop_path=self.stop,
            popen=self._popen(600),
            monotonic=self._monotonic,
        )
        self.assertEqual(result, 0)
        self.assertEqual(len(self.spawned), 2)
        self.assertTrue(all(p.signals == [STOP_SIGNAL] for p in self.spawned))


class ProcessLivenessTests(unittest.TestCase):
    """Both Windows liveness readings, reproduced rather than asserted."""

    def test_an_exited_child_reports_down_while_its_handle_is_still_open(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.exit(1)"],
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        child.wait()
        # Popen still holds the handle here. That is precisely what made a bare
        # OpenProcess succeed, and os.kill(pid, 0) raise nothing, for a process
        # that had already exited - the false-up reading this probe removes.
        self.assertFalse(process_liveness.pid_alive(child.pid))
        self.assertFalse(daemon_supervisor._is_alive(child.pid))

    def test_a_live_process_reports_up(self):
        self.assertTrue(process_liveness.pid_alive(os.getpid()))
        self.assertTrue(daemon_supervisor._is_alive(os.getpid()))

    def test_a_pid_that_never_existed_is_answered_rather_than_raised(self):
        # os.kill(4000000, 0) raises a bare OSError (WinError 87) on Windows.
        # The old guard caught only ProcessLookupError and PermissionError, so
        # that escaped main()'s startup check as a traceback.
        self.assertFalse(process_liveness.pid_alive(4000000))
        self.assertFalse(daemon_supervisor._is_alive(4000000))

    def test_values_that_are_not_pids_are_not_processes(self):
        for value in (None, 0, -1, True, False, "1234", 12.0, [7]):
            self.assertFalse(process_liveness.pid_alive(value), repr(value))

    def test_the_probe_has_no_control_capability(self):
        source = (
            Path(__file__).resolve().parents[1] / "src" / "process_liveness.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = {"subprocess", "socket", "signal", "board", "ledger", "urllib", "requests"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], forbidden)
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0], forbidden)
        # The handle is opened for query only, and nothing terminates with it.
        self.assertIn("PROCESS_QUERY_LIMITED_INFORMATION", source)
        self.assertNotIn("TerminateProcess", source)


if __name__ == "__main__":
    unittest.main()
