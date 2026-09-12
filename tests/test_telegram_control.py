"""Adversarial proofs for confirmed Telegram intake and board controls."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import board  # noqa: E402
import telegram_control  # noqa: E402
from support import BoardTestCase  # noqa: E402


class ControlFixture(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.tmp / "workspace"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Tri-AI Test"], cwd=self.repo, check=True)
        (self.repo / "README.md").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.repo, check=True)
        self.policy_path = self.tmp / "intake.json"
        self.policy_path.write_text(json.dumps({
            "verify_profiles": {
                "unittest": {"command": "python -m unittest", "timeout": 30},
            },
            "workspaces": {
                "demo": {"path": str(self.repo), "profile": "unittest"},
            },
        }), encoding="utf-8")
        self.control = telegram_control.TelegramControl(
            telegram_control.load_policy(self.policy_path)
        )
        self.ledger = self.tmp / "ledger.jsonl"
        self.runs = self.tmp / "runs"

    def dispatch(self, command: str, *, chat_id: str = "42") -> str:
        return self.control.dispatch(
            command, chat_id=chat_id, board_path=self.db_path,
            ledger_path=self.ledger, runs_root=self.runs,
        )

    def pending_id(self, response: str) -> str:
        return response.split()[2].rstrip(".")

    def task(self) -> str:
        return board.create_task(
            self.conn, title="failed", prompt="fix", repo=self.repo,
            verify_command="python -m unittest", verify_timeout=30,
        )


class ConfirmedIntake(ControlFixture):
    def test_run_is_pending_until_the_same_chat_confirms(self):
        response = self.dispatch("/run demo create a greeting")
        request_id = self.pending_id(response)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
        self.assertIn("forbidden", self.dispatch(f"/confirm {request_id}", chat_id="99").lower())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

        confirmed = self.dispatch(f"/confirm {request_id}")
        task_id = confirmed.split()[3]
        row = self.task_row(task_id)
        self.assertEqual(row["verify_command"], "python -m unittest")
        self.assertEqual(row["verify_timeout"], 30)
        self.assertEqual(row["workspace_path"], str(self.repo))
        self.assertIn("already confirmed", self.dispatch(f"/confirm {request_id}").lower())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)

    def test_multi_word_run_prompt_is_retained_as_one_pending_prompt(self):
        request_id = self.pending_id(self.dispatch("/run demo smoke test task"))
        payload = json.loads(self.conn.execute(
            "SELECT payload FROM triai_pending_actions WHERE id = ?", (request_id,)
        ).fetchone()[0])
        self.assertEqual(payload["prompt"], "smoke test task")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    def test_plain_text_creates_a_confirmed_intake_draft_in_the_default_workspace(self):
        request_id = self.pending_id(self.dispatch("smoke test task"))
        payload = json.loads(self.conn.execute(
            "SELECT payload FROM triai_pending_actions WHERE id = ?", (request_id,)
        ).fetchone()[0])
        self.assertEqual(payload["workspace"], str(self.repo))
        self.assertEqual(payload["prompt"], "smoke test task")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    def test_workspaces_and_help_expose_the_operator_owned_aliases(self):
        self.assertIn("demo (default)", self.dispatch("/workspaces"))
        help_text = self.dispatch("/help")
        self.assertIn("/workspaces", help_text)
        self.assertIn("/run <workspace-alias> <prompt>", help_text)

    def test_unknown_or_unconfigured_intake_is_reported_without_creating_a_pending_action(self):
        self.assertIn("Unknown workspace alias", self.dispatch("/run missing smoke test task"))
        disabled = telegram_control.TelegramControl().dispatch(
            "/run demo smoke test task", chat_id="42", board_path=self.db_path,
            ledger_path=self.ledger, runs_root=self.runs,
        )
        self.assertIn("not configured", disabled)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM triai_pending_actions").fetchone()[0], 0)

    def test_expired_or_unknown_workspace_run_creates_no_task(self):
        self.assertIn("Unknown workspace alias", self.dispatch("/run C:\\outside prompt"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

        now = int(time.time())
        board.create_pending_action(
            self.conn, action_id="p_expired", chat_id="42", action="run",
            payload={
                "title": "expired", "prompt": "x", "workspace": str(self.repo),
                "verify_command": "python -m unittest", "verify_timeout": 30,
            },
            expires_at=now + 1,
        )
        expired = board.confirm_pending_action(
            self.conn, action_id="p_expired", chat_id="42", now=now + 1
        )
        self.assertEqual(expired.status, "expired")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    def test_invalid_policy_refuses_escape_and_unknown_profile_before_use(self):
        escaped = self.tmp / "escape-policy.json"
        escaped.write_text(json.dumps({
            "verify_profiles": {"p": {"command": "x", "timeout": 1}},
            "workspaces": {"bad": {"path": str(self.tmp / "missing"), "profile": "p"}},
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not an existing Git"):
            telegram_control.load_policy(escaped)
        unknown = self.tmp / "unknown-policy.json"
        unknown.write_text(json.dumps({
            "verify_profiles": {},
            "workspaces": {"bad": {"path": str(self.repo), "profile": "missing"}},
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unknown profile"):
            telegram_control.load_policy(unknown)

        no_default = self.tmp / "no-default-policy.json"
        no_default.write_text(json.dumps({
            "verify_profiles": {"p": {"command": "x", "timeout": 1}},
            "workspaces": {
                "first": {"path": str(self.repo), "profile": "p"},
                "second": {"path": str(self.repo), "profile": "p"},
            },
        }), encoding="utf-8")
        policy = telegram_control.load_policy(no_default)
        control = telegram_control.TelegramControl(policy)
        response = control.dispatch(
            "plain task prompt", chat_id="42", board_path=self.db_path,
            ledger_path=self.ledger, runs_root=self.runs,
        )
        self.assertIn("No default workspace", response)


class RetryAndCancel(ControlFixture):
    def test_retry_requires_confirmation_and_preserves_the_verify_spec(self):
        task_id = self.task()
        self.kb.block_task(self.conn, task_id, reason="test", kind="needs_input")
        before = dict(self.task_row(task_id))
        runs_before = self.conn.execute(
            "SELECT COUNT(*) FROM task_runs WHERE task_id = ?", (task_id,)
        ).fetchone()[0]
        request_id = self.pending_id(self.dispatch(f"/retry {task_id}"))
        self.assertEqual(self.task_row(task_id)["status"], "blocked")
        self.dispatch(f"/confirm {request_id}")
        after = dict(self.task_row(task_id))
        self.assertEqual(after["status"], "ready")
        self.assertEqual(after["verify_command"], before["verify_command"])
        self.assertEqual(after["verify_timeout"], before["verify_timeout"])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM task_runs WHERE task_id = ?", (task_id,)
        ).fetchone()[0], runs_before)

    def test_cancel_uses_claim_identity_and_cannot_overwrite_a_completion_race(self):
        task_id = self.task()
        claimed = self.kb.claim_task(self.conn, task_id, claimer=self.host_local_claimer(12345))
        self.assertIsNotNone(claimed)
        self.kb._set_worker_pid(self.conn, task_id, 12345)

        raced = board.cancel_task(
            self.conn, task_id,
            signal_fn=lambda pid, sig: self.kb.complete_task(self.conn, task_id, result="won race"),
        )
        self.assertFalse(raced.changed)
        self.assertEqual(self.task_row(task_id)["status"], "done")

        second = self.task()
        self.assertIsNotNone(self.kb.claim_task(self.conn, second, claimer=self.host_local_claimer(12346)))
        self.kb._set_worker_pid(self.conn, second, 12346)
        cancelled = board.cancel_task(
            self.conn, second, signal_fn=lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError(pid))
        )
        self.assertTrue(cancelled.changed)
        self.assertEqual(self.task_row(second)["status"], "cancelled")
        run = self.conn.execute(
            "SELECT status, outcome FROM task_runs WHERE task_id = ?", (second,)
        ).fetchone()
        self.assertEqual((run["status"], run["outcome"]), ("cancelled", "cancelled"))

    def test_cancel_refuses_a_running_claim_without_a_registered_pid(self):
        task_id = self.task()
        self.assertIsNotNone(self.kb.claim_task(self.conn, task_id, claimer=self.host_local_claimer(12347)))
        refused = board.cancel_task(self.conn, task_id)
        self.assertFalse(refused.changed)
        self.assertEqual(refused.status, "unknown")
        self.assertEqual(self.task_row(task_id)["status"], "running")


class ProposalCallbacks(ControlFixture):
    def callback(self, data: str, *, chat_id: str = "42"):
        return self.control.dispatch_callback(
            data, chat_id=chat_id, board_path=self.db_path,
            ledger_path=self.ledger, runs_root=self.runs,
        )

    def test_callback_accepts_only_registered_decisions_and_is_idempotent(self):
        task_id = self.task()
        claimed = self.kb.claim_task(self.conn, task_id, claimer=self.host_local_claimer(12700))
        self.assertIsNotNone(claimed)
        self.assertTrue(self.kb.complete_task(self.conn, task_id, expected_run_id=claimed.current_run_id))
        created = board.create_task_outcome_proposal(
            self.conn, task_id=task_id, run_id=claimed.current_run_id, title="failed", outcome="passed",
        )
        approved = self.callback(f"prop:approve:{created.proposal_id}")
        self.assertTrue(approved.remove_buttons)
        self.assertIn("Approved", approved.text)
        self.assertEqual(self.task_row(task_id)["status"], "archived")
        repeated = self.callback(f"prop:approve:{created.proposal_id}")
        self.assertTrue(repeated.remove_buttons)
        self.assertIn("Already approved", repeated.text)
        self.assertTrue(self.callback("prop:approve:bad id").remove_buttons)
        self.assertIn("unsafe", self.callback("run this").text.lower())


class TelegramControlBoundary(unittest.TestCase):
    source = Path(__file__).resolve().parents[1] / "src" / "telegram_control.py"

    def test_control_has_no_process_or_raw_task_sql(self):
        tree = ast.parse(self.source.read_text(encoding="utf-8"))
        forbidden_imports = {"subprocess", "os", "socket"}
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                violations.extend(alias.name for alias in node.names if alias.name.split(".")[0] in forbidden_imports)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "execute":
                    violations.append(f"raw execute at {node.lineno}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
