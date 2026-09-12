"""Proofs for the read-only Phase 6 JARVIS terminal dashboard."""

from __future__ import annotations

import ast
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import board  # noqa: E402
from dashboard import jarvis_terminal as jarvis  # noqa: E402
from support import BoardTestCase  # noqa: E402


class DashboardFixture(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ledger_path = self.tmp / "ledger.jsonl"
        self.state_path = self.tmp / "daemons.json"
        self.done = board.create_task(
            self.conn, title="completed root", prompt="fixture", verify_command="true", verify_timeout=30,
        )
        self.ready = board.create_task(
            self.conn, title="ready child", prompt="fixture", verify_command="true", verify_timeout=30,
            parents=(self.done,),
        )
        self.running = board.create_task(
            self.conn, title="running sibling", prompt="fixture", verify_command="true", verify_timeout=30,
        )
        self.kb.complete_task(self.conn, self.done, result="passed")
        self.assertIsNotNone(self.kb.claim_task(self.conn, self.running, claimer="fixture:7"))
        self.ledger_path.write_text(
            "\n".join((
                json.dumps({"ts": 1.0, "task_id": self.done, "outcome": "passed", "verify_exit": 0}),
                json.dumps({"ts": 2.0, "task_id": self.running, "outcome": "failed", "verify_exit": 7}),
                "{not-json}",
            )) + "\n",
            encoding="utf-8",
        )
        self.state_path.write_text(json.dumps({
            "status": "running", "supervisor_pid": 100, "worker_pid": 101, "telegram_pid": 102,
        }), encoding="utf-8")

    def snapshot(self) -> jarvis.DashboardSnapshot:
        return jarvis.read_snapshot(
            board_path=self.db_path,
            ledger_path=self.ledger_path,
            daemon_state_path=self.state_path,
            ledger_limit=2,
            pid_alive=lambda pid: pid in {100, 101},
        )


class DashboardSnapshotTests(DashboardFixture):
    def test_snapshot_reads_board_graph_ledger_and_daemon_health(self):
        snapshot = self.snapshot()
        task_statuses = {task.task_id: task.status for task in snapshot.tasks}
        self.assertEqual(task_statuses, {
            self.done: "done", self.ready: "ready", self.running: "running",
        })
        self.assertEqual(snapshot.edges, (jarvis.TaskEdge(self.done, self.ready),))
        self.assertEqual([event.task_id for event in snapshot.ledger_events], [self.running, self.done])
        self.assertEqual(snapshot.ledger_errors, ("ledger line 3 is not valid JSON",))
        self.assertEqual(snapshot.daemons.status, "running")
        self.assertEqual(snapshot.daemons.processes, (("supervisor", True), ("worker", True), ("telegram", False)))

    def test_rich_rendering_contains_only_snapshot_evidence(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=150, color_system=None)
        jarvis.render_snapshot(self.snapshot(), console=console)
        rendered = stream.getvalue()
        self.assertIn("TRI-AI JARVIS", rendered)
        self.assertIn(self.done, rendered)
        self.assertIn(self.ready, rendered)
        self.assertIn(f"{self.done} -> {self.ready}", rendered)
        self.assertIn("supervisor: up", rendered)
        self.assertIn("telegram: down", rendered)
        self.assertIn("ledger line 3 is not valid JSON", rendered)

    def test_snapshot_cannot_change_the_board_file(self):
        before = self.db_path.read_bytes()
        self.snapshot()
        self.assertEqual(self.db_path.read_bytes(), before)

    def test_missing_board_is_an_honest_source_error(self):
        with self.assertRaisesRegex(jarvis.DashboardSourceError, "board database does not exist"):
            jarvis.read_snapshot(
                board_path=self.tmp / "missing.db", ledger_path=self.ledger_path,
                daemon_state_path=self.state_path,
            )

    def test_cli_renders_a_snapshot_without_writing(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = jarvis.main([
                "--board", str(self.db_path), "--ledger", str(self.ledger_path),
                "--daemon-state", str(self.state_path), "--ledger-limit", "2",
            ])
        self.assertEqual(result, 0, stderr.getvalue())
        self.assertIn("TRI-AI JARVIS", stdout.getvalue())
        self.assertIn(self.done, stdout.getvalue())

    def test_cli_returns_nonzero_for_a_missing_board(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = jarvis.main([
                "--board", str(self.tmp / "missing.db"), "--ledger", str(self.ledger_path),
                "--daemon-state", str(self.state_path),
            ])
        self.assertEqual(result, 1)
        self.assertIn("board database does not exist", stderr.getvalue())


class DashboardBoundaryTests(unittest.TestCase):
    source = Path(__file__).resolve().parents[1] / "src" / "dashboard" / "jarvis_terminal.py"

    def test_dashboard_has_no_board_mutation_network_or_spawn_capability(self):
        tree = ast.parse(self.source.read_text(encoding="utf-8"))
        forbidden_modules = {"board", "ledger", "subprocess", "socket", "requests", "urllib", "httpx"}
        imports = []
        dml = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names if alias.name.split(".")[0] in forbidden_modules)
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in forbidden_modules:
                    imports.append(node.module)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute":
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    if node.args[0].value.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER")):
                        dml.append(node.args[0].value)
        self.assertEqual(imports, [])
        self.assertEqual(dml, [])

if __name__ == "__main__":
    unittest.main()
