"""Phase 3 Slice 1: planner graph persistence and write-time rejection."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import BoardTestCase  # noqa: E402

import planner  # noqa: E402


class PlannerGraphWriter(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()

    def graph(self):
        workspace = {"kind": "dir", "path": str(self.repo)}
        return {
            "nodes": [
                {
                    "node_key": "lint",
                    "title": "lint",
                    "prompt": "run lint",
                    "agent_role": "reviewer",
                    "capabilities": ["codebase_memory", "security_review"],
                    "workspace": workspace,
                    "verify_command": "python -c \"raise SystemExit(0)\"",
                    "verify_timeout": 30,
                    "expected_artifacts": [],
                    "parents": [],
                },
                {
                    "node_key": "test",
                    "title": "test",
                    "prompt": "run tests",
                    "workspace": workspace,
                    "verify_command": "python -c \"raise SystemExit(0)\"",
                    "verify_timeout": 30,
                    "expected_artifacts": [],
                    "parents": [],
                },
                {
                    "node_key": "package",
                    "title": "package",
                    "prompt": "package the result",
                    "workspace": workspace,
                    "verify_command": "python -c \"raise SystemExit(0)\"",
                    "verify_timeout": 30,
                    "expected_artifacts": [],
                    "parents": ["lint", "test"],
                },
            ]
        }

    def task_count(self):
        return self.conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]

    def test_graph_is_persisted_with_real_parent_links(self):
        ids = planner.write_graph(self.conn, self.graph())
        self.assertEqual(set(ids), {"lint", "test", "package"})
        self.assertEqual(self.task_count(), 3)
        self.assertEqual(
            set(self.kb.parent_ids(self.conn, ids["package"])),
            {ids["lint"], ids["test"]},
        )
        self.assertEqual(self.task_row(ids["lint"])["status"], "ready")
        self.assertEqual(self.task_row(ids["package"])["status"], "todo")
        self.assertEqual(
            self.task_row(ids["package"])["verify_command"],
            "python -c \"raise SystemExit(0)\"",
        )
        lint_spec = planner.board.verify_spec(self.conn, ids["lint"])
        self.assertEqual(lint_spec["agent_role"], "reviewer")
        self.assertEqual(
            lint_spec["capabilities"], ["codebase_memory", "security_review"]
        )
        self.kb.complete_task(self.conn, ids["lint"], result="lint passed")
        self.assertEqual(
            self.task_row(ids["package"])["status"],
            "todo",
            "one completed parent must not make a fan-in child ready",
        )
        self.kb.complete_task(self.conn, ids["test"], result="tests passed")
        self.assertEqual(self.task_row(ids["package"])["status"], "ready")

    def test_graph_nodes_without_manual_capabilities_are_inferred(self):
        graph = self.graph()
        graph["nodes"][1]["prompt"] = "Design and browser-test a polished dashboard"

        ids = planner.write_graph(self.conn, graph)

        spec = planner.board.verify_spec(self.conn, ids["test"])
        self.assertIn("taste", spec["capabilities"])
        self.assertIn("frontend_engineering", spec["capabilities"])
        self.assertIn("browser_qa", spec["capabilities"])

    def test_missing_oracle_rejects_the_whole_graph_before_any_write(self):
        graph = self.graph()
        graph["nodes"][1]["verify_command"] = " "
        with self.assertRaises(planner.GraphValidationError):
            planner.write_graph(self.conn, graph)
        self.assertEqual(self.task_count(), 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM task_links").fetchone()["n"],
            0,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE verify_command IS NULL").fetchone()["n"],
            0,
        )

    def test_a_mid_graph_write_failure_rolls_back_every_prior_node(self):
        real_create = planner.board.create_task
        calls = 0

        def fail_on_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("deliberate graph write failure")
            return real_create(*args, **kwargs)

        planner.board.create_task = fail_on_second
        try:
            with self.assertRaisesRegex(RuntimeError, "deliberate graph write failure"):
                planner.write_graph(self.conn, self.graph())
        finally:
            planner.board.create_task = real_create
        self.assertEqual(self.task_count(), 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM task_links").fetchone()["n"],
            0,
        )

    def test_invalid_graph_forms_are_rejected_without_partial_rows(self):
        cases = []
        unknown = self.graph()
        unknown["nodes"][2]["parents"] = ["missing"]
        cases.append(unknown)
        duplicate = self.graph()
        duplicate["nodes"][1]["node_key"] = "lint"
        cases.append(duplicate)
        cycle = self.graph()
        cycle["nodes"][0]["parents"] = ["package"]
        cases.append(cycle)
        timeout = self.graph()
        timeout["nodes"][0]["verify_timeout"] = 0
        cases.append(timeout)
        escape = self.graph()
        escape["nodes"][0]["expected_artifacts"] = ["..\\outside.txt"]
        cases.append(escape)
        blank_artifact = self.graph()
        blank_artifact["nodes"][0]["expected_artifacts"] = [" "]
        cases.append(blank_artifact)
        unknown_capability = self.graph()
        unknown_capability["nodes"][0]["capabilities"] = ["unlimited_shell"]
        cases.append(unknown_capability)
        forbidden_for_role = self.graph()
        forbidden_for_role["nodes"][0]["agent_role"] = "researcher"
        forbidden_for_role["nodes"][0]["capabilities"] = ["deployment_prepare"]
        cases.append(forbidden_for_role)

        for graph in cases:
            with self.subTest(graph=graph):
                with self.assertRaises(planner.GraphValidationError):
                    planner.write_graph(self.conn, graph)
                self.assertEqual(self.task_count(), 0)

    def test_worktree_branch_cannot_be_reused_within_a_graph(self):
        graph = self.graph()
        for index, node in enumerate(graph["nodes"][:2]):
            node["workspace"] = {
                "kind": "worktree",
                "path": str(self.repo),
                "branch": "phase3-shared",
            }
            node["node_key"] = f"node-{index}"
        graph["nodes"][2]["parents"] = ["node-0", "node-1"]
        with self.assertRaises(planner.GraphValidationError):
            planner.write_graph(self.conn, graph)
        self.assertEqual(self.task_count(), 0)

    def test_board_rejects_incomplete_explicit_workspace_fields(self):
        with self.assertRaises(ValueError):
            planner.board.create_task(
                self.conn,
                title="bad worktree",
                prompt="x",
                verify_command="true",
                workspace_kind="worktree",
                workspace_path=self.repo,
            )
        with self.assertRaises(ValueError):
            planner.board.create_task(
                self.conn,
                title="orphan path",
                prompt="x",
                verify_command="true",
                workspace_path=self.repo,
            )
        self.assertEqual(self.task_count(), 0)

    def test_cli_exits_after_persisting_the_graph(self):
        graph_path = self.tmp / "graph.json"
        graph_path.write_text(json.dumps(self.graph()), encoding="utf-8")
        source = Path(__file__).resolve().parents[1] / "src" / "planner.py"
        env = os.environ.copy()
        env["TRIAI_BOARD_DB"] = str(self.db_path)
        result = subprocess.run(
            [sys.executable, str(source), "--graph", str(graph_path), "--board", str(self.db_path)],
            cwd=str(self.repo),
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.task_count(), 3)
        self.assertEqual(set(json.loads(result.stdout)), {"lint", "test", "package"})


if __name__ == "__main__":
    unittest.main()
