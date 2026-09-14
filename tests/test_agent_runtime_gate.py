"""A verify command is never run over a turn that did not happen.

Run 26 of `t_669fec6c` is the case this closes. The model router selected
`gemma4:e4b`, the provider returned 404, and hermes printed that error as its
final response and exited **0**. The worker read the exit code, saw success,
and ran the verify command against a workspace the agent had not touched. The
sandbox verifier of the day asked only whether a deliverable existed anywhere
in the repo - one did, from an earlier run - so it exited 0 and the task
reached `done` having produced nothing. Zero artifacts were captured, which was
the only honest signal anywhere in the record, and nothing consumed it.

Exit status cannot see this. What can is the runtime's own usage file, which
carried `"failed": true, "model": null`. That is not the agent reporting on its
work - the thesis rightly refuses that as evidence - it is the runtime
recording that the turn never completed.

The task is backed off as an environment fault rather than failed outright: a
router picking a model the provider does not serve is not the task's fault, and
routing it through the logic breaker would blame a task for an outage.

Everything here is real - board, git, the ledger - except the two runners.
"""

from __future__ import annotations

import json
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


RUN_26_OUTPUT = "API call failed after 3 retries: HTTP 404: model 'gemma4:e4b' not found\n"


class AgentRuntimeGate(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ledger = self.tmp / "ledger.jsonl"
        self.runs = self.tmp / "runs"
        self.repo = self.tmp / "workspace"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "TriAI Test"], cwd=self.repo, check=True)
        # Output from an EARLIER run, already committed. This is what made the
        # old sandbox gate pass forever, and what the verify double stands in
        # for here: a verifier that inspects repository state, not a diff.
        (self.repo / "celestial.html").write_text("<h1>from run 24</h1>", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "earlier run's deliverable"], cwd=self.repo, check=True)
        self.verify_calls: list[str] = []

    def task(self) -> str:
        return board.create_task(
            self.conn, title="add a toggle", prompt="add a toggle to celestial.html",
            verify_command="python verify.py", repo=self.repo, verify_timeout=60,
        )

    @contextmanager
    def runners(self, *, runtime_failed, agent_exit=0, agent_writes=(),
                agent_output=None):
        def run_agent(*_args, **_kwargs):
            for name, body in agent_writes:
                (self.repo / name).write_text(body, encoding="utf-8")
            out = agent_output or (
                RUN_26_OUTPUT if runtime_failed else "agent ran\n"
            )
            return executor.AgentResult(
                agent_exit, out, 1.0, runtime_failed=runtime_failed,
            )

        def run_verify(*_args, **_kwargs):
            # Stands in for a state-inspecting verifier: it passes because
            # celestial.html is in the repo, regardless of what this run did.
            self.verify_calls.append("ran")
            return executor.VerifyResult("passed", 0, "verify: 1 deliverable present\n", 0.2)

        with mock.patch.object(executor, "run_agent", side_effect=run_agent), \
             mock.patch.object(executor, "run_verify", side_effect=run_verify):
            yield

    def run_task(self, task_id: str, **kwargs):
        claimed = self.kb.claim_task(self.conn, task_id, claimer=worker.worker_id())
        self.assertIsNotNone(claimed)
        with self.runners(**kwargs):
            return worker.execute_task(
                self.conn, claimed, ledger_path=self.ledger, runs_root=self.runs,
            )

    # -- the defect --------------------------------------------------------

    def test_a_runtime_that_never_completed_does_not_reach_done(self):
        tid = self.task()
        attempt = self.run_task(tid, runtime_failed=True)
        self.assertNotEqual(
            self.task_row(tid)["status"], "done",
            "a task whose agent never ran was accepted as complete",
        )
        self.assertNotEqual(attempt.outcome, "passed")

    def test_verify_is_never_invoked_when_the_runtime_reports_failure(self):
        # The gate is upstream of verify on purpose. A verifier handed an
        # unchanged workspace cannot distinguish this run from the last one.
        self.run_task(self.task(), runtime_failed=True)
        self.assertEqual(
            self.verify_calls, [],
            "the verify command was given a workspace the agent never touched",
        )

    def test_exit_zero_is_not_taken_as_evidence_the_turn_happened(self):
        # Run 26's exact shape: process exit 0, runtime failed. An exit-code
        # check alone passes this; that is why the gate reads the usage record.
        attempt = self.run_task(self.task(), runtime_failed=True, agent_exit=0)
        self.assertNotEqual(attempt.outcome, "passed")

    def test_it_is_an_environment_fault_not_the_tasks_fault(self):
        tid = self.task()
        attempt = self.run_task(tid, runtime_failed=True)
        self.assertIn("environment", attempt.outcome)
        self.assertEqual(attempt.entry["failure_class"], "environment")
        self.assertNotEqual(
            self.task_row(tid)["status"], "running",
            "the task was left stranded in running",
        )

    def test_the_reason_carries_what_the_runtime_actually_said(self):
        attempt = self.run_task(self.task(), runtime_failed=True)
        self.assertIn("404", attempt.entry["reason"])

    def test_partial_writes_from_a_failed_runtime_are_reverted(self):
        # A runtime can die mid-edit. The workspace must be returned to a known
        # state, exactly as it is on a failing verify.
        self.run_task(
            self.task(), runtime_failed=True,
            agent_writes=[("half-written.html", "<h1>incomp")],
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=self.repo,
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(status.stdout.strip(), "", "the failed run's writes were left behind")

    # -- the gate must not fire on anything else ---------------------------

    def test_a_completed_runtime_is_unaffected(self):
        tid = self.task()
        attempt = self.run_task(
            tid, runtime_failed=False, agent_writes=[("toggle.html", "<h1>x</h1>")],
        )
        self.assertEqual(attempt.outcome, "passed")
        self.assertEqual(self.task_row(tid)["status"], "done")

    def test_no_usage_record_after_a_clean_exit_leaves_verify_as_the_only_gate(self):
        # `None` + exit 0 means the runtime completed and simply wrote no usage
        # field. Absence of a record is not a record of failure, and the gate
        # must not invent one - older runtimes must keep working.
        tid = self.task()
        attempt = self.run_task(
            tid, runtime_failed=None, agent_exit=0,
            agent_writes=[("toggle.html", "<h1>x</h1>")],
        )
        self.assertEqual(attempt.outcome, "passed")
        self.assertEqual(self.verify_calls, ["ran"])

    # -- the launch-failure door (found by review, not by me) --------------
    #
    # The test above proves COMPATIBILITY for a runtime that completed. It says
    # nothing about SAFETY when the runtime never started, and the first version
    # of this suite stopped there - it wrote files and passed a verifier, so the
    # dangerous case was never exercised. These are that case.

    def test_a_launch_failure_with_no_usage_record_does_not_reach_done(self):
        # run_agent's containment-failure path verbatim: AgentResult(1, msg)
        # with no usage file, so runtime_failed stays None. Nothing was written
        # and the verifier still passes on celestial.html from the earlier run.
        tid = self.task()
        attempt = self.run_task(
            tid, runtime_failed=None, agent_exit=1,
            agent_output="FileNotFoundError: hermes.exe not found\n",
        )
        self.assertNotEqual(
            self.task_row(tid)["status"], "done",
            "a task whose agent never launched was accepted as complete",
        )
        self.assertNotEqual(attempt.outcome, "passed")

    def test_a_launch_failure_never_reaches_the_verify_command(self):
        self.run_task(
            self.task(), runtime_failed=None, agent_exit=1,
            agent_output="ContainmentError: job object refused\n",
        )
        self.assertEqual(
            self.verify_calls, [],
            "verify ran over a workspace no agent ever touched",
        )

    def test_a_timeout_with_no_usage_record_is_caught_too(self):
        # run_agent returns 124 on timeout; if the tree was killed before the
        # runtime could write usage, that is still a turn that did not complete.
        attempt = self.run_task(
            self.task(), runtime_failed=None, agent_exit=124,
            agent_output="agent TIMEOUT after 1800s; process tree terminated\n",
        )
        self.assertNotEqual(attempt.outcome, "passed")
        self.assertEqual(self.verify_calls, [])

    def test_the_launch_failure_reason_names_the_exit_code(self):
        attempt = self.run_task(
            self.task(), runtime_failed=None, agent_exit=1,
            agent_output="FileNotFoundError: hermes.exe not found\n",
        )
        self.assertIn("exited 1", attempt.entry["reason"])
        self.assertIn("hermes.exe", attempt.entry["reason"])


class UsageFileIsReadForCompletion(unittest.TestCase):
    """`run_agent` must surface the runtime's own failure record."""

    def _run(self, usage: dict | None, *, exit_code: int = 0):
        import tempfile

        class FakeProc:
            returncode = exit_code

            def __init__(self):
                # run_agent drains this on a reader thread; an iterable of
                # lines is what a real pipe presents.
                self.stdout = iter(["out\n"])

            def wait(self, timeout=None):
                return exit_code

        class FakeContained:
            def __init__(self, usage_path):
                self._usage_path = usage_path
                self.proc = FakeProc()

            def terminate_tree(self, grace=None):
                return True, []

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            usage_path = Path(tmp) / "usage.json"
            if usage is not None:
                usage_path.write_text(json.dumps(usage), encoding="utf-8")

            def spawn(*_args, **_kwargs):
                return FakeContained(usage_path)

            with mock.patch.object(executor, "spawn_contained", side_effect=spawn):
                return executor.run_agent(
                    Path(tmp), "do it", timeout=30, usage_path=usage_path,
                )

    def test_failed_true_is_surfaced(self):
        # Run 26's usage file, verbatim in the fields that matter.
        result = self._run({"failed": True, "completed": False, "model": None})
        self.assertIs(result.runtime_failed, True)
        self.assertEqual(result.exit_code, 0, "the process really did exit 0")

    def test_failed_false_is_surfaced(self):
        result = self._run({"failed": False, "completed": True, "model": "auto/best-free"})
        self.assertIs(result.runtime_failed, False)
        self.assertEqual(result.model_source, "usage_file")

    def test_a_missing_field_is_unknown_not_false(self):
        result = self._run({"model": "auto/best-free"})
        self.assertIsNone(result.runtime_failed)

    def test_a_missing_usage_file_is_unknown(self):
        result = self._run(None)
        self.assertIsNone(result.runtime_failed)


if __name__ == "__main__":
    unittest.main()
