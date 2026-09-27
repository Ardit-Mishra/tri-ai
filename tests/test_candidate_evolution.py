"""Adversarial proofs for draft-only constrained evolution."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memory import episodic, evolution  # noqa: E402


class CandidateEvolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = self.root / "ledger.jsonl"
        rows = [
            {"task_id": "a", "task_kind": "code", "failure_class": "logic", "verify_exit": 7},
            {"task_id": "b", "task_kind": "code", "failure_class": "logic", "verify_exit": 7},
            {"task_id": "c", "task_kind": "code", "failure_class": "environment", "verify_exit": 7},
            {"task_id": "d", "task_kind": "code", "failure_class": "logic", "verify_exit": 7},
        ]
        self.ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        self.conn = episodic.connect(self.root / "memory.db")
        self.addCleanup(self.conn.close)
        episodic.sync(self.conn, self.ledger)
        self.events = episodic.query(self.conn, source_path=self.ledger)

    def test_repeated_logic_failures_produce_a_draft_only_candidate(self):
        candidates = evolution.propose(self.events)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual((candidate.status, candidate.activation), ("draft", "operator_review_required"))
        self.assertTrue(evolution.valid(candidate))
        self.assertEqual(candidate.checks, ("inspect_last_verify_log", "inspect_verify_command"))

    def test_environment_and_single_failures_produce_no_candidate(self):
        self.assertEqual(evolution.propose(self.events[:3], minimum_occurrences=3), ())

    def test_invalid_threshold_is_refused_before_any_candidate_is_created(self):
        with self.assertRaises(ValueError):
            evolution.propose(self.events, minimum_occurrences=1)

    def test_empty_citation_candidate_is_inert(self):
        candidate = evolution.Candidate(
            "candidate:empty", "code", "logic", 7,
            ("inspect_last_verify_log",), (),
        )
        self.assertFalse(evolution.valid(candidate))

    def test_citation_drift_invalidates_candidate_and_replay_never_activates(self):
        candidate = evolution.propose(self.events)[0]
        self.ledger.write_text("{}\n", encoding="utf-8")
        self.assertFalse(evolution.valid(candidate))
        self.assertEqual(evolution.propose(self.events), ())
        replay = evolution.replay(candidate, self.events)
        self.assertFalse(replay.supported)
        self.assertEqual(candidate.status, "draft")

    def test_held_out_match_supports_but_does_not_activate_candidate(self):
        candidate = evolution.propose(self.events[:2])[0]
        replay = evolution.replay(candidate, [self.events[3]])
        self.assertTrue(replay.supported)
        self.assertEqual(candidate.activation, "operator_review_required")
        with self.assertRaises(FrozenInstanceError):
            candidate.status = "active"

    def test_source_evidence_cannot_be_replayed_as_held_out_support(self):
        candidate = evolution.propose(self.events[:2])[0]
        replay = evolution.replay(candidate, self.events[:2])
        self.assertFalse(replay.supported)


class CandidateEvolutionBoundaryTests(unittest.TestCase):
    def test_evolution_has_no_execution_or_mutation_imports(self):
        tree = ast.parse(Path(evolution.__file__).read_text(encoding="utf-8"))
        imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imports.update(node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module)
        self.assertTrue({"board", "worker", "executor", "routing_probe", "route_admission", "subprocess"}.isdisjoint(imports))
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertTrue({
            "complete_task", "create_task", "run_agent", "run_verify", "activate",
            "write_text", "write_bytes", "unlink", "replace", "rename",
        }.isdisjoint(attributes))
        calls = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue({"eval", "exec", "open", "__import__"}.isdisjoint(calls))


if __name__ == "__main__":
    unittest.main()
