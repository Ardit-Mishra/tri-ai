"""Proofs for deterministic semantic-memory extraction."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memory import episodic, procedural, semantic  # noqa: E402


class SemanticMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = self.root / "ledger.jsonl"
        rows = [
            {"task_id": "parent", "run_id": 1, "outcome": "passed"},
            {"task_id": "child", "run_id": 1, "outcome": "failed", "parents": ["parent"]},
            {"task_id": "child", "run_id": 2, "outcome": "passed", "parents": ["parent"]},
        ]
        self.ledger.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
        self.conn = episodic.connect(self.root / "memory.db")
        self.addCleanup(self.conn.close)
        episodic.sync(self.conn, self.ledger)
        self.events = episodic.query(self.conn, source_path=self.ledger)

    def test_derives_cited_dependency_outcome_and_retry_edges(self):
        graph = semantic.derive(reversed(self.events))
        edges = {(edge.source, edge.target, edge.relation) for edge in graph.edges}
        self.assertIn(("task:child", "task:parent", "depends_on"), edges)
        self.assertTrue(any(edge.relation == "produced" and edge.target == "outcome:failed" for edge in graph.edges))
        self.assertTrue(any(edge.relation == "retries_from" for edge in graph.edges))
        self.assertTrue(all(edge.citations for edge in graph.edges))

    def test_valid_rule_cites_source_and_stale_input_is_excluded(self):
        cited = self.events[1].citation
        rule = procedural.Rule(
            "observe-child", self.root, "code", ("inspect_last_verify_log",), (cited,), 9999999999.0
        )
        graph = semantic.derive(self.events, [rule])
        self.assertTrue(any(edge.relation == "cites" and edge.source == "rule:observe-child" for edge in graph.edges))

        self.ledger.write_text("{}\n", encoding="utf-8")
        stale = semantic.derive(self.events, [rule])
        self.assertEqual(stale.nodes, ())
        self.assertEqual(stale.edges, ())

    def test_graph_evidence_is_deterministic(self):
        self.assertEqual(semantic.derive(self.events).evidence(), semantic.derive(reversed(self.events)).evidence())


class SemanticMemoryBoundaryTests(unittest.TestCase):
    def test_semantic_memory_has_no_execution_capability(self):
        tree = ast.parse(Path(semantic.__file__).read_text(encoding="utf-8"))
        imports = {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        }
        imports.update(node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module)
        self.assertTrue({"board", "worker", "executor", "route_admission", "routing_probe", "subprocess"}.isdisjoint(imports))
        self.assertNotIn("complete_task", {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)})


if __name__ == "__main__":
    unittest.main()
