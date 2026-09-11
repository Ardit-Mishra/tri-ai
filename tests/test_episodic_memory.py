"""Exit-code proofs for the derived episodic-memory index."""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memory import episodic  # noqa: E402


def entry(task_id: str, outcome: str, failure_class: str | None = None) -> dict:
    return {
        "ts": 1.0,
        "task_id": task_id,
        "run_id": 1,
        "outcome": outcome,
        "failure_class": failure_class,
    }


class EpisodicMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = self.root / "ledger.jsonl"
        self.database = self.root / "memory.db"
        self.write(entry("one", "passed"))
        self.write(entry("two", "environment_backoff", "environment"))
        self.conn = episodic.connect(self.database)
        self.addCleanup(self.conn.close)

    def write(self, payload: dict) -> None:
        with self.ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def test_sync_creates_queryable_facts_with_exact_citations(self):
        summary = episodic.sync(self.conn, self.ledger)
        self.assertEqual(summary.current_events, 2)
        events = episodic.query(self.conn, source_path=self.ledger, outcome="passed")
        self.assertEqual([event.payload["task_id"] for event in events], ["one"])
        self.assertTrue(episodic.validate(events[0].citation))

    def test_append_preserves_prior_citation_but_line_change_invalidates_and_stales_it(self):
        episodic.sync(self.conn, self.ledger)
        original = episodic.query(self.conn, source_path=self.ledger, task_id="one")[0]
        self.write(entry("three", "passed"))
        episodic.sync(self.conn, self.ledger)
        self.assertTrue(episodic.validate(original.citation))

        lines = self.ledger.read_text(encoding="utf-8").splitlines()
        lines[0] = json.dumps(entry("one", "failed"), sort_keys=True)
        self.ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
        episodic.sync(self.conn, self.ledger)
        self.assertFalse(episodic.validate(original.citation))
        self.assertEqual(
            [event.payload["outcome"] for event in episodic.query(
                self.conn, source_path=self.ledger, task_id="one"
            )],
            ["failed"],
        )

    def test_malformed_source_hard_stops_before_current_index_mutation(self):
        episodic.sync(self.conn, self.ledger)
        before = episodic.query(self.conn, source_path=self.ledger)
        with self.ledger.open("a", encoding="utf-8") as handle:
            handle.write("{broken\n")
        with self.assertRaisesRegex(ValueError, "not JSON"):
            episodic.sync(self.conn, self.ledger)
        after = episodic.query(self.conn, source_path=self.ledger)
        self.assertEqual([event.payload for event in after], [event.payload for event in before])

    def test_current_filters_are_exact(self):
        episodic.sync(self.conn, self.ledger)
        events = episodic.query(
            self.conn,
            source_path=self.ledger,
            task_id="two",
            outcome="environment_backoff",
            failure_class="environment",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["task_id"], "two")


class EpisodicMemoryBoundaryTests(unittest.TestCase):
    def test_memory_has_no_execution_or_board_capability(self):
        tree = ast.parse(Path(episodic.__file__).read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertTrue(
            {"board", "worker", "executor", "routing_probe", "route_admission", "subprocess"}.isdisjoint(imported)
        )
        self.assertNotIn(
            "complete_task",
            {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)},
        )


if __name__ == "__main__":
    unittest.main()
