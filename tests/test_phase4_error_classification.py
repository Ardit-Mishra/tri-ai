"""Phase 4 Slice 3: deterministic environment-versus-logic handling.

The matrix drives ``worker.execute_task`` rather than a helper alone.  An
environment failure must be ledgered and delayed without burning the kernel
circuit breaker; a logic failure must still take the kernel's existing failure
path and block at its limit.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import board  # noqa: E402
import executor  # noqa: E402
import failure_class  # noqa: E402
import ledger  # noqa: E402
import worker  # noqa: E402
from failure_class import FailureClass  # noqa: E402
from support import BoardTestCase  # noqa: E402


class FailureClassificationTable(unittest.TestCase):
    """The deterministic signals used by the real worker path."""

    def classify(self, outcome: str, exit_code, output: str = ""):
        return failure_class.classify_failure(
            verify_outcome=outcome,
            verify_exit=exit_code,
            verify_output=output,
        )

    def test_environment_signals_are_classified_without_an_agent_report(self):
        cases = (
            ("spawn_error", None, "FileNotFoundError: verifier", "spawn"),
            ("timeout", None, "AssertionError printed before timeout", "timeout"),
            ("failed", 1, "MemoryError: cannot allocate memory", "oom"),
            ("failed", 1, "mirror: Connection reset by peer", "network"),
            ("failed", 1, "HTTP 429: quota exhausted", "quota"),
        )
        for outcome, exit_code, output, name in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    self.classify(outcome, exit_code, output),
                    FailureClass.ENVIRONMENT,
                )

    def test_logic_signals_remain_logic_by_default(self):
        for output in ("AssertionError: expected 1", "SyntaxError: invalid syntax", "tests failed"):
            with self.subTest(output=output):
                self.assertEqual(
                    self.classify("failed", 1, output), FailureClass.LOGIC,
                )

    def test_a_pass_or_unknown_outcome_is_not_silently_classified(self):
        with self.assertRaises(ValueError):
            self.classify("passed", 0, "ok")
        with self.assertRaises(ValueError):
            self.classify("invented", 1, "")


class EnvironmentBackoffThroughWorker(BoardTestCase):
    """Real board state, revert, task run, event and ledger evidence."""

    def setUp(self) -> None:
        super().setUp()
        self.ledger_path = self.tmp / "ledger.jsonl"
        self.runs_root = self.tmp / "runs"

    def _repo(self, name: str = "workspace") -> Path:
        repo = self.tmp / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Tri-AI Test"], cwd=repo, check=True)
        (repo / "baseline.txt").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
        return repo

    def _task(self, repo: Path) -> str:
        return board.create_task(
            self.conn,
            title="classified task",
            prompt="change nothing",
            verify_command="python -c \"import sys; sys.exit(1)\"",
            repo=repo,
            verify_timeout=30,
        )

    def _claim(self, task_id: str):
        claimed = self.kb.claim_task(self.conn, task_id, claimer=worker.worker_id())
        self.assertIsNotNone(claimed)
        return claimed

    @staticmethod
    def _agent() -> executor.AgentResult:
        return executor.AgentResult(0, "agent ran\n", 0.1)

    def _execute(self, task_id: str, result: executor.VerifyResult):
        claimed = self._claim(task_id)
        with mock.patch.object(executor, "run_agent", return_value=self._agent()), mock.patch.object(
            executor, "run_verify", return_value=result,
        ):
            return worker.execute_task(
                self.conn, claimed, ledger_path=self.ledger_path, runs_root=self.runs_root,
            )

    def test_environment_failures_are_ledgered_then_hidden_until_their_due_time(self):
        cases = (
            executor.VerifyResult("spawn_error", None, "FileNotFoundError", 0.1),
            executor.VerifyResult("timeout", None, "verify TIMEOUT", 0.1),
            executor.VerifyResult("failed", 1, "mirror: Connection reset by peer", 0.1),
            executor.VerifyResult("failed", 1, "HTTP 429: quota exhausted", 0.1),
        )
        for index, result in enumerate(cases):
            with self.subTest(outcome=result.outcome, output=result.output):
                repo = self._repo(f"workspace-{index}")
                task_id = self._task(repo)
                attempt = self._execute(task_id, result)

                self.assertEqual(attempt.outcome, "environment_backoff")
                row = self.task_row(task_id)
                self.assertEqual(row["status"], "ready")
                self.assertEqual(row["consecutive_failures"], 0)
                self.assertNotIn("gave_up", self.event_kinds(task_id))

                delay = board.environment_backoff(self.conn, task_id)
                self.assertIsNotNone(delay)
                self.assertEqual(delay["attempts"], 1)
                self.assertGreater(delay["eligible_at"], delay["created_at"])
                self.assertNotIn(task_id, {task["id"] for task in board.ready_tasks(self.conn)})
                self.assertIn(
                    task_id,
                    {task["id"] for task in board.ready_tasks(self.conn, now=delay["eligible_at"])},
                )

                entry = ledger.read_entries(self.ledger_path)[-1]
                self.assertEqual(entry["failure_class"], "environment")
                self.assertEqual(entry["verify_outcome"], result.outcome)
                self.assertEqual(entry["verify_exit"], result.exit_code)

    def test_environment_backoff_stops_after_three_retries_without_tripping_logic_breaker(self):
        repo = self._repo()
        task_id = self._task(repo)
        result = executor.VerifyResult("failed", 1, "HTTP 429: quota exhausted", 0.1)

        for number in range(1, worker.ENVIRONMENT_RETRY_LIMIT + 1):
            attempt = self._execute(task_id, result)
            self.assertEqual(attempt.outcome, "environment_backoff")
            self.assertEqual(self.task_row(task_id)["consecutive_failures"], 0)
            self.conn.execute(
                "UPDATE triai_environment_backoff SET eligible_at = 0 WHERE task_id = ?", (task_id,),
            )
            self.conn.commit()
            self.assertIn(number, range(1, worker.ENVIRONMENT_RETRY_LIMIT + 1))

        exhausted = self._execute(task_id, result)
        self.assertEqual(exhausted.outcome, "environment_exhausted")
        self.assertEqual(self.task_row(task_id)["status"], "blocked")
        self.assertEqual(self.task_row(task_id)["consecutive_failures"], 0)
        self.assertIn("environment_retry_exhausted", self.event_kinds(task_id))
        self.assertNotIn("gave_up", self.event_kinds(task_id))
        self.assertEqual(len(ledger.read_entries(self.ledger_path)), worker.ENVIRONMENT_RETRY_LIMIT + 1)

    def test_logic_failures_still_use_the_kernel_circuit_breaker(self):
        repo = self._repo()
        task_id = self._task(repo)
        result = executor.VerifyResult("failed", 1, "AssertionError: expected 1", 0.1)

        first = self._execute(task_id, result)
        self.assertEqual(first.outcome, "failed")
        self.assertEqual(self.task_row(task_id)["status"], "ready")
        self.assertEqual(self.task_row(task_id)["consecutive_failures"], 1)

        second = self._execute(task_id, result)
        self.assertEqual(second.outcome, "failed")
        self.assertEqual(self.task_row(task_id)["status"], "blocked")
        self.assertEqual(self.task_row(task_id)["consecutive_failures"], 2)
        self.assertIn("gave_up", self.event_kinds(task_id))
        self.assertTrue(all(entry["failure_class"] == "logic" for entry in ledger.read_entries(self.ledger_path)))

    def test_a_logic_failure_resets_the_consecutive_environment_retry_series(self):
        repo = self._repo()
        task_id = self._task(repo)
        environment = executor.VerifyResult("failed", 1, "HTTP 429: quota exhausted", 0.1)
        logic = executor.VerifyResult("failed", 1, "AssertionError: expected 1", 0.1)

        self.assertEqual(self._execute(task_id, environment).outcome, "environment_backoff")
        self.conn.execute(
            "UPDATE triai_environment_backoff SET eligible_at = 0 WHERE task_id = ?", (task_id,),
        )
        self.conn.commit()
        self.assertEqual(self._execute(task_id, logic).outcome, "failed")
        self.assertIsNone(board.environment_backoff(self.conn, task_id))

        self.assertEqual(self._execute(task_id, environment).outcome, "environment_backoff")
        self.assertEqual(board.environment_backoff(self.conn, task_id)["attempts"], 1)


if __name__ == "__main__":
    unittest.main()
