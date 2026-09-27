"""Adversarial tests for durable Telegram proposal review and activation."""

from __future__ import annotations

import ast
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import board  # noqa: E402
import proposals  # noqa: E402
from memory import episodic, evolution, procedural  # noqa: E402
from support import BoardTestCase  # noqa: E402


class ProposalReviewTests(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()

    def task(self, title: str = "task") -> str:
        return board.create_task(
            self.conn, title=title, prompt="work", repo=self.workspace,
            verify_command="python -m unittest", verify_timeout=30,
        )

    def done_task(self) -> str:
        task_id = self.task("done task")
        claimed = self.kb.claim_task(self.conn, task_id, claimer=self.host_local_claimer(6101))
        self.assertIsNotNone(claimed)
        self.assertTrue(self.kb.complete_task(self.conn, task_id, expected_run_id=claimed.current_run_id))
        return task_id

    def candidate(self):
        source = self.tmp / "ledger.jsonl"
        lines = [
            json.dumps({"task_id": "a", "task_kind": "code", "failure_class": "logic", "verify_exit": 7}),
            json.dumps({"task_id": "b", "task_kind": "code", "failure_class": "logic", "verify_exit": 7}),
        ]
        source.write_text("\n".join(lines) + "\n", encoding="utf-8")
        memory = episodic.connect(self.tmp / "memory.db")
        self.addCleanup(memory.close)
        episodic.sync(memory, source)
        return source, evolution.propose(episodic.query(memory, source_path=source))[0]

    def test_completed_task_proposal_archives_only_the_done_task_and_double_tap_is_inert(self):
        task_id = self.done_task()
        created = board.create_task_outcome_proposal(
            self.conn, task_id=task_id, run_id=1, title="done task", outcome="passed",
        )
        self.assertTrue(created.changed)
        approved = board.decide_proposal(self.conn, proposal_id=created.proposal_id, decision="approve")
        self.assertTrue(approved.changed)
        self.assertEqual(self.task_row(task_id)["status"], "archived")
        repeated = board.decide_proposal(self.conn, proposal_id=created.proposal_id, decision="approve")
        self.assertFalse(repeated.changed)
        self.assertEqual(repeated.status, "approved")

    def test_failed_task_proposal_uses_the_existing_retry_gate(self):
        task_id = self.task("failed task")
        self.kb.block_task(self.conn, task_id, reason="operator review", kind="needs_input")
        created = board.create_task_outcome_proposal(
            self.conn, task_id=task_id, run_id=1, title="failed task", outcome="failed",
        )
        approved = board.decide_proposal(self.conn, proposal_id=created.proposal_id, decision="approve")
        self.assertTrue(approved.changed)
        self.assertEqual(self.task_row(task_id)["status"], "ready")
        self.assertEqual(board.proposal(self.conn, created.proposal_id)["status"], "approved")

    def test_candidate_activation_persists_only_a_validated_rule_and_drift_expires_it(self):
        source, candidate = self.candidate()
        created = proposals.create_candidate_proposals(
            self.conn, [candidate], workspace=self.workspace, expires_at=time.time() + 60,
        )[0]
        source.write_text("{}\n", encoding="utf-8")
        expired = board.decide_proposal(self.conn, proposal_id=created.proposal_id, decision="approve")
        self.assertTrue(expired.changed)
        self.assertEqual(expired.status, "expired")
        self.assertIsNone(board.activated_procedural_rule(self.conn, created.proposal_id))

    def test_candidate_activation_is_operator_only_and_rejection_is_inert(self):
        _, candidate = self.candidate()
        created = proposals.create_candidate_proposals(
            self.conn, [candidate], workspace=self.workspace, expires_at=time.time() + 60,
        )[0]
        self.assertIsNone(board.activated_procedural_rule(self.conn, created.proposal_id))
        rejected = board.decide_proposal(self.conn, proposal_id=created.proposal_id, decision="reject")
        self.assertTrue(rejected.changed)
        self.assertIsNone(board.activated_procedural_rule(self.conn, created.proposal_id))

    def test_evolution_scan_creates_pending_candidate_proposals_without_activation(self):
        source, _ = self.candidate()
        memory = episodic.connect(self.tmp / "scan-memory.db")
        self.addCleanup(memory.close)
        episodic.sync(memory, source)
        created = proposals.generate_candidate_proposals(
            self.conn, episodic.query(memory, source_path=source),
            workspace=self.workspace, expires_at=time.time() + 60,
        )
        self.assertEqual(len(created), 1)
        self.assertEqual(board.proposal(self.conn, created[0].proposal_id)["status"], "pending")
        self.assertIsNone(board.activated_procedural_rule(self.conn, created[0].proposal_id))

    def test_command_shaped_candidate_payload_is_refused_before_any_proposal_write(self):
        _, candidate = self.candidate()
        rule = proposals.candidate_rule_mapping(
            candidate, workspace=self.workspace, expires_at=time.time() + 60,
        )
        rule["checks"] = ["git push origin main"]
        with self.assertRaises(ValueError):
            board.create_candidate_rule_proposal(
                self.conn, proposal_id="evolution:unsafe", rule=rule,
            )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM triai_proposals").fetchone()[0], 0)

    def test_approved_candidate_becomes_selectable_procedural_advice_not_execution(self):
        _, candidate = self.candidate()
        created = proposals.create_candidate_proposals(
            self.conn, [candidate], workspace=self.workspace, expires_at=time.time() + 60,
        )[0]
        approved = board.decide_proposal(self.conn, proposal_id=created.proposal_id, decision="approve")
        self.assertTrue(approved.changed)
        raw_rule = board.activated_procedural_rule(self.conn, created.proposal_id)
        self.assertIsNotNone(raw_rule)
        rule = procedural.rule_from_mapping(raw_rule)
        advice = procedural.select(
            [rule], workspace=self.workspace, task_kind="code", max_characters=200,
        )
        self.assertEqual(advice.checks, candidate.checks)

    def test_notification_receipts_are_per_chat_and_do_not_change_the_proposal(self):
        task_id = self.done_task()
        created = board.create_task_outcome_proposal(
            self.conn, task_id=task_id, run_id=1, title="done task", outcome="passed",
        )
        self.assertEqual(len(board.pending_proposals_for_chat(self.conn, "42")), 1)
        self.assertTrue(board.record_proposal_notification(
            self.conn, proposal_id=created.proposal_id, chat_id="42", message_id=99,
        ))
        self.assertEqual(board.pending_proposals_for_chat(self.conn, "42"), ())
        self.assertEqual(len(board.pending_proposals_for_chat(self.conn, "43")), 1)
        self.assertEqual(board.proposal(self.conn, created.proposal_id)["status"], "pending")


class ProposalBoundaryTests(unittest.TestCase):
    def test_proposal_adapter_has_no_execution_or_worker_import(self):
        source = Path(proposals.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module.split(".")[0]
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertTrue({"worker", "executor", "subprocess", "routing_probe"}.isdisjoint(imported))
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertTrue({"run_agent", "run_verify", "complete_task"}.isdisjoint(attributes))


if __name__ == "__main__":
    unittest.main()
