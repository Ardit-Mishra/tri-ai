"""Adversarial proofs for bounded procedural memory."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memory import procedural  # noqa: E402


def digest(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


class ProceduralMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.ledger = self.root / "ledger.jsonl"
        self.line = json.dumps({"task_id": "one", "outcome": "failed"}, sort_keys=True)
        self.ledger.write_text(self.line + "\n", encoding="utf-8")
        self.now = 1000.0

    def raw_rule(self, **changes):
        rule = {
            "id": "check-verify",
            "workspace": str(self.workspace),
            "task_kind": "code",
            "checks": ["confirm_workspace_clean", "inspect_last_verify_log"],
            "citations": [{
                "source_path": str(self.ledger),
                "source_line": 1,
                "line_digest": digest(self.line),
            }],
            "expires_at": self.now + 10,
            "activation": "preflight_advice",
        }
        rule.update(changes)
        return rule

    def test_matching_cited_rule_returns_only_its_fixed_checklist(self):
        rule = procedural.rule_from_mapping(self.raw_rule(), now=self.now)
        advice = procedural.select(
            [rule], workspace=self.workspace, task_kind="code", max_characters=200, now=self.now
        )
        self.assertEqual(advice.rule_ids, ("check-verify",))
        self.assertEqual(advice.checks, ("confirm_workspace_clean", "inspect_last_verify_log"))

    def test_stale_expired_or_scope_mismatch_rule_is_excluded(self):
        rule = procedural.rule_from_mapping(self.raw_rule(), now=self.now)
        self.ledger.write_text("{}\n", encoding="utf-8")
        advice = procedural.select(
            [rule], workspace=self.workspace, task_kind="code", max_characters=200, now=self.now
        )
        self.assertEqual(advice.rule_ids, ())
        self.assertIn("check-verify", advice.excluded_rule_ids)

        expired = procedural.rule_from_mapping(self.raw_rule(expires_at=self.now + 1), now=self.now)
        advice = procedural.select(
            [expired], workspace=self.workspace, task_kind="code", max_characters=200, now=self.now + 2
        )
        self.assertEqual(advice.rule_ids, ())

    def test_free_text_commands_unknown_fields_and_wrong_activation_are_refused(self):
        for changes in (
            {"advice": "git push"},
            {"checks": ["git push"]},
            {"activation": "route_policy"},
            {"workspace": "relative-workspace"},
            {"citations": [{
                "source_path": "relative-ledger.jsonl",
                "source_line": 1,
                "line_digest": digest(self.line),
            }]},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    procedural.rule_from_mapping(self.raw_rule(**changes), now=self.now)

    def test_expired_historical_rule_loads_but_remains_inert(self):
        path = self.root / "rules.json"
        path.write_text(json.dumps({"rules": [self.raw_rule(expires_at=self.now - 1)]}), encoding="utf-8")
        rules = procedural.load_rules(path, now=self.now)
        advice = procedural.select(
            rules, workspace=self.workspace, task_kind="code", max_characters=200, now=self.now
        )
        self.assertEqual(advice.rule_ids, ())
        self.assertEqual(advice.excluded_rule_ids, ("check-verify",))

    def test_loading_and_selection_are_deterministic_and_bounded(self):
        first = self.raw_rule(id="b", checks=["inspect_last_verify_log"])
        second = self.raw_rule(id="a", checks=["confirm_workspace_clean"])
        path = self.root / "rules.json"
        path.write_text(json.dumps({"rules": [first, second]}), encoding="utf-8")
        rules = procedural.load_rules(path, now=self.now)
        advice = procedural.select(
            rules, workspace=self.workspace, task_kind="code", max_characters=25, now=self.now
        )
        self.assertEqual(advice.rule_ids, ("a",))
        self.assertEqual(advice.checks, ("confirm_workspace_clean",))
        self.assertIn("b", advice.excluded_rule_ids)


class ProceduralMemoryBoundaryTests(unittest.TestCase):
    def test_procedural_memory_has_no_execution_or_routing_capability(self):
        tree = ast.parse(Path(procedural.__file__).read_text(encoding="utf-8"))
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
            {
                "board", "worker", "executor", "routing_probe", "route_admission", "subprocess",
            }.isdisjoint(imported)
        )
        self.assertNotIn(
            "complete_task",
            {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)},
        )


if __name__ == "__main__":
    unittest.main()
