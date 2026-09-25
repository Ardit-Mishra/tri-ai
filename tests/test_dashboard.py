"""Proofs for the read-only Phase 6 JARVIS terminal dashboard."""

from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import board  # noqa: E402
from dashboard import jarvis_terminal as jarvis  # noqa: E402
from memory import brain  # noqa: E402
from support import BoardTestCase  # noqa: E402


class DashboardFixture(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ledger_path = self.tmp / "ledger.jsonl"
        self.state_path = self.tmp / "daemons.json"
        self.done = board.create_task(
            self.conn, title="completed root", prompt="fixture", repo=self.tmp,
            verify_command="true", verify_timeout=30,
        )
        self.ready = board.create_task(
            self.conn, title="ready child", prompt="fixture", repo=self.tmp,
            verify_command="true", verify_timeout=30,
            parents=(self.done,),
        )
        self.running = board.create_task(
            self.conn, title="running sibling", prompt="fixture", repo=self.tmp,
            verify_command="true", verify_timeout=30,
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

    def test_snapshot_reads_brain_metadata_without_mutating_it(self):
        brain_path = self.tmp / "brain" / "brain.db"
        memory = brain.connect(brain_path)
        first = brain.capture(memory, "Tri-AI and the dashboard are one system.", source="operator")
        second = brain.capture(memory, "Telegram is the primary mobile intake.", source="operator")
        brain.link(
            memory, first.item_id, second.item_id,
            relation="receives_commands_from", evidence_item_id=second.item_id,
        )
        memory.close()
        before = brain_path.read_bytes()

        snapshot = jarvis.read_snapshot(
            board_path=self.db_path,
            ledger_path=self.ledger_path,
            daemon_state_path=self.state_path,
            brain_path=brain_path,
            pid_alive=lambda pid: False,
        )

        self.assertEqual(snapshot.brain.item_count, 2)
        self.assertEqual(snapshot.brain.inbox_count, 2)
        self.assertEqual(snapshot.brain.edge_count, 1)
        self.assertEqual(snapshot.brain.items[0].item_id, second.item_id)
        self.assertEqual(snapshot.brain.edges[0].relation, "receives_commands_from")
        self.assertEqual(brain_path.read_bytes(), before)


class DashboardSnapshotTests(DashboardFixture):
    def test_snapshot_reads_capability_and_radar_evidence_without_mutation(self):
        catalog_path = self.tmp / "capabilities" / "catalog.json"
        catalog_path.parent.mkdir(parents=True)
        catalog_path.write_text(json.dumps({
            "schema": "triai.capability-catalog.v1",
            "resources": [
                {"resource_id": "skill:one", "name": "One", "kind": "skill", "availability": "active"},
                {"resource_id": "skill:old", "name": "Old", "kind": "skill", "availability": "archived-reference"},
                {"resource_id": "adapter:browser-use", "name": "Browser Use", "kind": "command", "availability": "executable", "adapter_status": "gated", "health_status": "entrypoint-present", "risk_status": "reviewed", "tags": ["browser"]},
            ],
        }), encoding="utf-8")
        radar_path = self.tmp / "radar" / "latest.json"
        radar_path.parent.mkdir(parents=True)
        radar_path.write_text(json.dumps({
            "schema": "triai.technology-radar.v1", "generated_at": "2026-09-21T00:00:00Z",
            "candidates": [{"name": "example/new-tool", "source": "github", "url": "https://github.com/example/new-tool", "disposition": "evaluate", "stars": 2, "signals": ["recency"]}],
            "evaluations": [{"candidate": {"name": "example/new-tool"}, "disposition": "probe-incomplete", "static": {"verdict": "static_clear"}, "dynamic": {"status": "not_run"}}],
            "errors": [],
        }), encoding="utf-8")
        before_catalog, before_radar = catalog_path.read_bytes(), radar_path.read_bytes()

        snapshot = jarvis.read_snapshot(
            board_path=self.db_path, ledger_path=self.ledger_path,
            daemon_state_path=self.state_path, capability_catalog_path=catalog_path,
            radar_path=radar_path, pid_alive=lambda pid: False,
        )

        self.assertEqual(snapshot.capabilities.total, 3)
        self.assertEqual(snapshot.capabilities.active, 1)
        self.assertEqual(snapshot.capabilities.archived, 1)
        self.assertEqual(snapshot.capabilities.items[0].name, "Browser Use")
        self.assertEqual(snapshot.radar.candidate_count, 1)
        self.assertEqual(snapshot.radar.evaluations[0].static_verdict, "static_clear")
        self.assertEqual(catalog_path.read_bytes(), before_catalog)
        self.assertEqual(radar_path.read_bytes(), before_radar)

    def test_default_liveness_probe_recognizes_the_current_process(self):
        self.assertTrue(jarvis._pid_alive(os.getpid()))

    @unittest.skipUnless(os.name == "nt", "Windows process-object lifetime behaviour")
    def test_exited_process_with_a_lingering_handle_reports_down(self):
        """A handle that still opens is not liveness; the exit code decides."""
        # Windows keeps the process object while Popen holds its handle, so
        # OpenProcess keeps succeeding after the child is gone. Reporting that
        # as "up" would show a dead daemon as healthy.
        child = subprocess.Popen([sys.executable, "-c", "raise SystemExit(0)"])
        self.addCleanup(child.wait)
        child.wait()
        self.assertFalse(jarvis._pid_alive(child.pid))

    def test_snapshot_projects_run_phases_from_recorded_evidence(self):
        by_id = {task.task_id: task for task in self.snapshot().tasks}
        running = by_id[self.running].telemetry
        self.assertIsNotNone(running)
        self.assertEqual(running.run_status, "running")
        phases = {phase.key: phase for phase in running.phases}
        self.assertEqual(set(phases), set(jarvis.PHASE_ORDER))
        self.assertIn(phases["claimed"].state, {"done", "active"})
        # The fixture uses a directory workspace, so there is no worktree stage
        # to reach; it must be reported as skipped rather than pending.
        self.assertEqual(phases["worktree_prep"].state, "skipped")
        self.assertIn("no worktree stage", phases["worktree_prep"].evidence)
        # A task that never ran must not be given a fabricated lifecycle.
        ready = by_id[self.ready].telemetry
        self.assertIsNone(ready.run_id)
        self.assertEqual(ready.logs, ())

    def test_active_run_logs_are_read_from_the_runs_root_under_a_bound(self):
        """The active run's retained log is tailed, bounded, and never summarised."""
        claimed = next(task for task in self.snapshot().tasks if task.task_id == self.running)
        run_id = claimed.telemetry.run_id
        self.assertIsNotNone(run_id, "fixture claim must record a run")
        run_dir = self.tmp / "runs" / self.running / str(run_id)
        run_dir.mkdir(parents=True)
        (run_dir / "agent.log").write_text(
            "\n".join(f"line {index}" for index in range(1, 120)) + "\n", encoding="utf-8",
        )
        snapshot = jarvis.read_snapshot(
            board_path=self.db_path,
            ledger_path=self.ledger_path,
            daemon_state_path=self.state_path,
            ledger_limit=2,
            pid_alive=lambda pid: False,
            runs_root=self.tmp / "runs",
        )
        running = next(task for task in snapshot.tasks if task.task_id == self.running)
        logs = {log.name: log for log in running.telemetry.logs}
        self.assertEqual(set(logs), {"agent"})
        self.assertEqual(len(logs["agent"].lines), jarvis.LOG_TAIL_LINES)
        self.assertEqual(logs["agent"].lines[-1], "line 119")
        self.assertTrue(logs["agent"].truncated)
        # A finished task must not have its logs read at all.
        done = next(task for task in snapshot.tasks if task.task_id == self.done)
        self.assertEqual(done.telemetry.logs, ())

    def test_snapshot_reads_board_graph_ledger_and_daemon_health(self):
        snapshot = self.snapshot()
        task_statuses = {task.task_id: task.status for task in snapshot.tasks}
        self.assertEqual(task_statuses, {
            self.done: "done", self.ready: "ready", self.running: "running",
        })
        self.assertEqual(snapshot.edges, (jarvis.TaskEdge(self.done, self.ready),))
        self.assertEqual([event.task_id for event in snapshot.ledger_events], [self.running, self.done])
        self.assertEqual(snapshot.ledger_entry_count, 2)
        self.assertEqual(snapshot.ledger_errors, ("ledger line 3 is not valid JSON",))
        self.assertEqual(snapshot.daemons.status, "running")
        self.assertEqual(snapshot.daemons.processes, (("supervisor", True), ("worker", True), ("telegram", False)))

    def test_snapshot_projects_accepted_rule_checks_and_provenance(self):
        proposal_id = "evolution:fixture"
        raw_rule = {
            "id": "fixture-clean-workspace",
            "workspace": str(self.tmp),
            "task_kind": "code",
            "checks": ["confirm_workspace_clean", "inspect_last_verify_log"],
            "citations": [{
                "source_path": str(self.ledger_path), "source_line": 1,
                "line_digest": "a" * 64,
            }],
            "expires_at": 9999999999,
            "activation": "preflight_advice",
        }
        self.conn.execute(
            "INSERT INTO triai_proposals "
            "(id, kind, summary, suggested_action, payload_json, status, created_at) "
            "VALUES (?, 'candidate_rule', 'fixture', 'activate_procedural_advice', '{}', 'approved', 1)",
            (proposal_id,),
        )
        self.conn.execute(
            "INSERT INTO triai_activated_procedural_rules (proposal_id, rule_json, activated_at) "
            "VALUES (?, ?, 1)", (proposal_id, json.dumps(raw_rule)),
        )
        self.conn.commit()

        snapshot = self.snapshot()

        self.assertEqual(snapshot.activated_rule_count, 1)
        self.assertEqual(snapshot.memory_errors, ())
        self.assertEqual(snapshot.rules[0].rule_id, "fixture-clean-workspace")
        self.assertEqual(snapshot.rules[0].checks, ("confirm_workspace_clean", "inspect_last_verify_log"))
        self.assertEqual(snapshot.rules[0].citations[0].source_line, 1)
        self.assertEqual(snapshot.tasks[0].workspace_path, str(self.tmp))

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
