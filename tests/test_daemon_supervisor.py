"""Hermetic proofs for the local operational daemon supervisor."""

from __future__ import annotations

import ast
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import daemon_supervisor  # noqa: E402


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
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


class DaemonSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_commands_are_fixed_daemon_argv_with_optional_local_policy_only(self):
        result = daemon_supervisor.commands(
            root=self.root, board_path=self.root / "board.db", ledger_path=self.root / "ledger.jsonl",
            runs_root=self.root / "runs", intake_policy=self.root / "intake.json",
        )
        self.assertIn("worker_daemon.py", result.worker[1])
        self.assertIn("telegram_daemon.py", result.telegram[1])
        self.assertIn("--intake-policy", result.telegram)
        self.assertNotIn("shell", " ".join((*result.worker, *result.telegram)).lower())

    def test_rotation_retains_old_log_without_deleting_it(self):
        log = self.root / "worker.log"
        log.write_text("old evidence", encoding="utf-8")
        daemon_supervisor.rotate_log(log, limit_bytes=1)
        self.assertFalse(log.exists())
        archived = list(self.root.glob("worker-*.log"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_text(encoding="utf-8"), "old evidence")

    def test_stop_request_reaches_both_children_and_records_both_pids(self):
        stop = self.root / "stop.request"
        stop.touch()
        spawned = []
        captured = []

        def popen(*args, **kwargs):
            process = FakeProcess(100 + len(spawned))
            spawned.append(process)
            return process

        result = daemon_supervisor.supervise(
            daemon_supervisor.commands(
                root=self.root, board_path=self.root / "board.db", ledger_path=self.root / "ledger.jsonl",
                runs_root=self.root / "runs", intake_policy=None,
            ),
            log_dir=self.root / "logs", run_id="test", stop_path=stop, popen=popen,
            on_started=lambda children: captured.extend(child.pid for child in children),
        )
        self.assertEqual(result, 0)
        self.assertEqual(captured, [100, 101])
        self.assertTrue(all(process.returncode == 0 for process in spawned))

    def test_second_start_failure_stops_the_first_child(self):
        first = FakeProcess(100)
        calls = 0

        def popen(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return first
            raise OSError("Telegram launch failed")

        with self.assertRaises(OSError):
            daemon_supervisor.supervise(
                daemon_supervisor.commands(
                    root=self.root, board_path=self.root / "board.db", ledger_path=self.root / "ledger.jsonl",
                    runs_root=self.root / "runs", intake_policy=None,
                ),
                log_dir=self.root / "logs", run_id="test", stop_path=self.root / "no-stop", popen=popen,
            )
        self.assertEqual(first.returncode, 0)

    def test_missing_telegram_environment_fails_before_state_or_child_spawn(self):
        log_dir = self.root / "logs"
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {
            "TRI_AI_TELEGRAM_BOT_TOKEN": "",
            "TRI_AI_TELEGRAM_AUTHORIZED_CHAT_ID": "",
            "TRI_AI_TELEGRAM_AUTHORIZED_CHAT_IDS": "",
        }, clear=False), redirect_stderr(stderr):
            result = daemon_supervisor.main([
                "--board", str(self.root / "board.db"),
                "--ledger", str(self.root / "ledger.jsonl"),
                "--runs-dir", str(self.root / "runs"),
                "--log-dir", str(log_dir),
            ])

        self.assertEqual(result, 2)
        self.assertIn("TRI_AI_TELEGRAM_BOT_TOKEN", stderr.getvalue())
        self.assertIn("TRI_AI_TELEGRAM_AUTHORIZED_CHAT_ID", stderr.getvalue())
        self.assertFalse((log_dir / "daemons.json").exists())


class DaemonScriptBoundaryTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]

    def test_supervisor_has_no_shell_true_or_unbounded_child_command(self):
        source = (self.root / "src" / "daemon_supervisor.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertNotIn("shell=True", source.replace(" ", ""))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        self.assertFalse(any(
            isinstance(call.func, ast.Attribute) and call.func.attr in {"run", "call", "check_call"}
            for call in calls
        ))

    def test_powershell_scripts_delegate_to_the_supervisor_and_stop_sentinel(self):
        start = (self.root / "scripts" / "run_daemons.ps1").read_text(encoding="utf-8")
        stop = (self.root / "scripts" / "stop_daemons.ps1").read_text(encoding="utf-8")
        self.assertIn("daemon_supervisor.py", start)
        self.assertIn("$WhatIf", start)
        self.assertNotIn("Start-Process", start)
        self.assertIn("stop_path", stop)
        self.assertNotIn("Stop-Process", stop)

    def test_powershell_runner_defaults_to_tri_ai_runtime_paths(self):
        script = self.root / "scripts" / "run_daemons.ps1"
        completed = subprocess.run(
            [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(script), "-WhatIf",
            ],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        expected_root = Path.home() / ".tri-ai"
        self.assertIn(str(expected_root / "board.db"), completed.stdout)
        self.assertIn(str(expected_root / "ledger.jsonl"), completed.stdout)
        self.assertIn(str(expected_root / "runs"), completed.stdout)


if __name__ == "__main__":
    unittest.main()
