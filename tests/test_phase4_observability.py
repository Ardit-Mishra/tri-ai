"""Proofs for the local, read-only Phase 4 phone-command adapter."""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import board  # noqa: E402
import ledger  # noqa: E402
import telegram_read_surface as surface  # noqa: E402
from support import BoardTestCase  # noqa: E402


class ObservabilityFixture(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ledger_path = self.tmp / "ledger.jsonl"
        self.runs_root = self.tmp / "runs"
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Tri-AI Test"], cwd=self.repo, check=True)
        (self.repo / "baseline.txt").write_text("ok\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.repo, check=True)

    def task(self, title: str) -> str:
        return board.create_task(self.conn, title=title, prompt="inspect", repo=self.repo,
                                 verify_command="python -c \"import sys; sys.exit(0)\"", verify_timeout=20)

    def entry(self, task_id: str, *, run_id: int = 1, outcome: str = "failed") -> None:
        paths = ledger.run_output_paths(task_id, run_id, root=self.runs_root)
        paths[0].write_text("agent full output\n" + "a" * 3000, encoding="utf-8")
        paths[1].write_text("verify full stderr\n" + "b" * 3000, encoding="utf-8")
        ledger.record({
            "ts": 1.0, "worker": "host:1", "task_id": task_id, "run_id": run_id,
            "title": "fixture", "repo": str(self.repo), "branch": "main", "outcome": outcome,
            "verify_exit": 7, "verify_outcome": "failed", "agent_exit": 0,
            "model": None, "provider": None, "model_source": "unavailable", "seconds": 1.0,
            "reason": "deliberate", "failure_class": "logic",
            "agent_log": str(paths[0]), "verify_log": str(paths[1]),
        }, path=self.ledger_path)


class ReadCommandsReflectEvidence(ObservabilityFixture):
    def test_status_matches_the_direct_board_rows_and_shows_a_ledger_gap(self):
        task_id = self.task("running-without-ledger")
        claimed = self.kb.claim_task(self.conn, task_id, claimer="host:77")
        self.assertIsNotNone(claimed)
        output = surface.render_status(self.db_path, self.ledger_path)
        self.assertIn("tasks=1 (running=1)", output)
        self.assertIn(task_id, output)
        self.assertIn("Ledger entries: 0", output)

    def test_task_renders_exact_board_and_ledger_evidence(self):
        task_id = self.task("failed-task")
        self.entry(task_id)
        output = surface.render_task(self.db_path, self.ledger_path, task_id)
        self.assertIn("Status: ready", output)
        self.assertIn("Ledger entries: 1", output)
        self.assertIn("verify_exit=7", output)
        self.assertIn("verify_outcome=failed", output)

    def test_logs_return_full_retained_output_not_paths_or_a_tail(self):
        task_id = self.task("logs-task")
        self.entry(task_id)
        output = surface.render_logs(self.ledger_path, task_id, runs_root=self.runs_root)
        self.assertIn("a" * 3000, output)
        self.assertIn("b" * 3000, output)

    def test_logs_refuse_a_forged_path_outside_task_runs(self):
        task_id = self.task("forged-log")
        outside = self.tmp / "outside.log"
        outside.write_text("must not disclose", encoding="utf-8")
        ledger.record({"task_id": task_id, "run_id": 1, "agent_log": str(outside), "verify_log": str(outside)}, path=self.ledger_path)
        output = surface.render_logs(self.ledger_path, task_id, runs_root=self.runs_root)
        self.assertNotIn("must not disclose", output)
        self.assertIn("outside task runs", output)

    def test_dispatcher_exposes_no_write_verb(self):
        task_id = self.task("command-task")
        before = dict(self.task_row(task_id))
        self.assertIn("Unknown", surface.dispatch_command("/retry " + task_id, board_path=self.db_path,
                                                            ledger_path=self.ledger_path, runs_root=self.runs_root))
        self.assertEqual(dict(self.task_row(task_id))["status"], before["status"])


class ReadSurfaceHasNoMutationCapability(unittest.TestCase):
    source = Path(__file__).resolve().parents[1] / "src" / "telegram_read_surface.py"

    def test_ast_has_no_network_process_or_board_mutator(self):
        tree = ast.parse(self.source.read_text(encoding="utf-8"))
        forbidden_modules = {"subprocess", "socket", "requests", "urllib", "httpx"}
        forbidden_board_calls = {"create_task", "clear_quarantine", "quarantine_workspace", "record_worktree"}
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                violations.extend(alias.name for alias in node.names if alias.name.split(".")[0] in forbidden_modules)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "board" and node.func.attr in forbidden_board_calls:
                    violations.append("board." + node.func.attr)
        self.assertEqual(violations, [])

    def test_a_deliberately_bad_network_import_is_detectable(self):
        bad = ast.parse("import requests\ndef broken():\n    requests.get('https://example.invalid')\n")
        imports = [alias.name for node in ast.walk(bad) if isinstance(node, ast.Import) for alias in node.names]
        self.assertIn("requests", imports)


if __name__ == "__main__":
    unittest.main()
