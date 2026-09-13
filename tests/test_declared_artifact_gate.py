"""Phase 7 criteria 3 and 4, on the real worker path.

Criterion 4: a verify command proves the command. It says nothing about whether
the deliverable a task promised exists. A task that declares expected artifacts
and does not produce them fails, even on verify exit 0.

Criterion 3: artifacts are read the moment the agent exits, so output the verify
command itself creates is never recorded as the agent's work.

Like the verify-gate suite this builds on, everything is real - board, git, the
ledger - except ``executor.run_agent`` and ``executor.run_verify``.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from support import BoardTestCase  # noqa: E402

import board  # noqa: E402
import executor  # noqa: E402
import worker  # noqa: E402


class DeclaredArtifactGate(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ledger = self.tmp / "ledger.jsonl"
        self.runs = self.tmp / "runs"
        self.repo = self.tmp / "workspace"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "TriAI Test"], cwd=self.repo, check=True)
        (self.repo / "marker.txt").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.repo, check=True)

    def task(self, *, declares=()) -> str:
        return board.create_task(
            self.conn, title="build a report", prompt="build it",
            verify_command="python verify.py", repo=self.repo, verify_timeout=60,
            expected_artifacts=list(declares),
        )

    def claim(self, task_id: str):
        claimed = self.kb.claim_task(self.conn, task_id, claimer=worker.worker_id())
        self.assertIsNotNone(claimed)
        return claimed

    @contextmanager
    def runners(self, *, agent_writes=(), verify_writes=(), verify=None):
        """Doubles that write files the way a real agent and verifier would."""
        def run_agent(*_args, **_kwargs):
            for name, body in agent_writes:
                (self.repo / name).write_text(body, encoding="utf-8")
            return executor.AgentResult(0, "agent ran\n", 1.0)

        def run_verify(*_args, **_kwargs):
            for name, body in verify_writes:
                (self.repo / name).write_text(body, encoding="utf-8")
            return verify or executor.VerifyResult("passed", 0, "ok\n", 0.2)

        with mock.patch.object(executor, "run_agent", side_effect=run_agent), \
             mock.patch.object(executor, "run_verify", side_effect=run_verify):
            yield

    def run_task(self, task_id: str, **kwargs):
        claimed = self.claim(task_id)
        with self.runners(**kwargs):
            return worker.execute_task(
                self.conn, claimed, ledger_path=self.ledger, runs_root=self.runs,
            )

    # -- criterion 4 -------------------------------------------------------

    def test_a_declared_artifact_that_was_never_produced_fails_a_passing_verify(self):
        tid = self.task(declares=["report.html"])
        attempt = self.run_task(tid, agent_writes=[("notes.txt", "something else")])
        self.assertEqual(attempt.outcome, "failed")
        self.assertNotEqual(self.task_row(tid)["status"], "done")

    def test_a_declared_artifact_that_is_empty_fails(self):
        tid = self.task(declares=["report.html"])
        attempt = self.run_task(tid, agent_writes=[("report.html", "")])
        self.assertEqual(attempt.outcome, "failed")

    def test_a_declared_artifact_that_exists_passes(self):
        tid = self.task(declares=["report.html"])
        attempt = self.run_task(tid, agent_writes=[("report.html", "<h1>done</h1>")])
        self.assertEqual(attempt.outcome, "passed")
        self.assertEqual(self.task_row(tid)["status"], "done")

    def test_declaring_nothing_leaves_the_verify_command_as_the_only_gate(self):
        # Most tasks declare nothing; the gate must not invent a requirement.
        tid = self.task()
        attempt = self.run_task(tid, agent_writes=[("anything.txt", "x")])
        self.assertEqual(attempt.outcome, "passed")

    def test_the_gate_cannot_rescue_a_failing_verify(self):
        # Producing the declared file does not make a failed command pass.
        tid = self.task(declares=["report.html"])
        attempt = self.run_task(
            tid,
            agent_writes=[("report.html", "<h1>done</h1>")],
            verify=executor.VerifyResult("failed", 2, "boom\n", 0.2),
        )
        self.assertEqual(attempt.outcome, "failed")

    # -- criterion 3 -------------------------------------------------------

    def test_only_the_agents_output_is_recorded_not_the_verifiers(self):
        # The verify command writing its own report must not be attributed to
        # the agent. This is the attribution defect the phase exists to close.
        tid = self.task()
        attempt = self.run_task(
            tid,
            agent_writes=[("deliverable.html", "<h1>made</h1>")],
            verify_writes=[("coverage.xml", "<coverage/>")],
        )
        self.assertEqual(attempt.outcome, "passed")
        recorded = {row["path"] for row in board.run_artifacts(self.conn, task_id=tid)}
        self.assertIn("deliverable.html", recorded)
        self.assertNotIn(
            "coverage.xml", recorded,
            "the verify command's own output was attributed to the agent",
        )


if __name__ == "__main__":
    unittest.main()
