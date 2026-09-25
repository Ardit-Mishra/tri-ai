"""Phase 2, criterion 1: the worker processes exactly one task, and the verify
command — never the agent's report — gates success and failure.

This drives worker.execute_task over a REAL, temp board row: claim -> precheck
(real git on a temp repo) -> upstream-artifact gate -> agent -> verify ->
accept/revert -> ledger. The only two executor gateways that reach out of the
process are swapped for doubles — ``executor.run_agent`` and
``executor.run_verify`` — so no real Hermes runs and no task is accepted except
by the verify result the test injects.

What is asserted is that the board row's TERMINAL status is exactly what verify
decided:

    passed       -> status 'done'   (run closed, ledger outcome 'passed')
    failed       -> status 'ready'  (reverted; run closed 'failed', retryable)
    timeout      -> status 'ready'  (recorded WITHOUT borrowing an exit code)
    spawn_error  -> status 'ready'  (recorded as its own outcome, not a generic
                                     failure — the discriminated vocabulary)
    upstream gate failed -> the agent is NEVER invoked; status 'ready'

And one whole-invocation check: ``run_once`` claims and executes at most one
task no matter how many are ready.
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
import ledger  # noqa: E402
import worker  # noqa: E402


def _git(cwd, *argv):
    return subprocess.run(["git", *argv], cwd=cwd, capture_output=True, text=True)


class WorkerVerifyGate(BoardTestCase):
    """Per-case temp repo, ledger and runs tree; the executor doubles are per-test."""

    def setUp(self) -> None:
        super().setUp()
        self.ledger = self.tmp / "ledger.jsonl"
        self.runs = self.tmp / "runs"

    # -- helpers ---------------------------------------------------------

    def _make_repo(self, name: str = "workspace") -> Path:
        """A git repo with an initial commit.

        The initial commit is load-bearing, not decoration: ``git stash push``
        on a repo that has NO commit fails with exit 1 ("You do not have the
        initial commit yet"), which would send the failed-verify path down the
        ``revert_failed`` quarantine-stop instead of the revert-and-retry path
        this criterion is about.
        """
        repo = self.tmp / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"],
                       cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "TriAI Test"],
                       cwd=repo, check=True)
        (repo / "marker.txt").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
        return repo

    def _commit(self, repo: Path, message: str) -> None:
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)

    def _make_task(self, repo: Path, *, title: str, verify: str,
                   parents: list[str] | tuple[()] | None = None) -> str:
        return board.create_task(
            self.conn,
            title=title,
            prompt="make a changelog",
            verify_command=verify,
            repo=repo,
            verify_timeout=60,
            parents=list(parents or ()),
        )

    def _claim(self, task_id: str):
        claimed = self.kb.claim_task(self.conn, task_id, claimer=worker.worker_id())
        self.assertIsNotNone(claimed, f"claim of {task_id} failed")
        self.assertEqual(self.task_row(task_id)["status"], "running")
        return claimed

    def _agent(self, exit_code: int = 0) -> executor.AgentResult:
        return executor.AgentResult(exit_code, "agent ran\n", 1.2)

    @contextmanager
    def _patch_runners(self, agent, verify):
        with mock.patch.object(executor, "run_agent", return_value=agent) as ra, \
             mock.patch.object(executor, "run_verify", return_value=verify) as rv:
            yield ra, rv

    # -- verify pass -----------------------------------------------------

    def test_verify_exit_zero_completes_the_task(self):
        repo = self._make_repo()
        tid = self._make_task(repo, title="write changelog", verify="python verify.py")
        claimed = self._claim(tid)

        agent = self._agent(0)
        verify = executor.VerifyResult("passed", 0, "verify ok\n", 0.3)
        with self._patch_runners(agent, verify) as (ra, rv):
            attempt = worker.execute_task(
                self.conn, claimed, ledger_path=self.ledger, runs_root=self.runs,
            )

        self.assertEqual(attempt.outcome, "passed")
        row = self.task_row(tid)
        self.assertEqual(row["status"], "done")
        self.assertEqual(ra.call_count, 1)
        self.assertEqual(rv.call_count, 1)
        self.assertIn("completed", self.event_kinds(tid))

        entries = ledger.read_entries(self.ledger)
        self.assertEqual(len(entries), 1, "one task, one ledger line")
        self.assertEqual(entries[0]["outcome"], "passed")
        self.assertEqual(entries[0]["verify_exit"], 0)
        self.assertEqual(entries[0]["verify_outcome"], "passed")
        self.assertEqual(entries[0]["task_id"], tid)
        proposal = board.proposal(self.conn, f"task:{tid}:{claimed.current_run_id}:passed")
        self.assertIsNotNone(proposal)
        self.assertEqual((proposal["status"], proposal["suggested_action"]), ("pending", "archive"))

    def test_the_agent_gets_the_board_prompt_and_repo(self):
        repo = self._make_repo()
        tid = self._make_task(repo, title="write changelog", verify="python verify.py")
        claimed = self._claim(tid)

        seen = {}
        agent = self._agent(0)
        with mock.patch.object(
            executor, "run_agent",
            side_effect=lambda repo_arg, prompt, timeout, usage_path=None,
            on_activity=None: (
                seen.update(repo=repo_arg, prompt=prompt, timeout=timeout),
                agent,
            )[1],
        ), mock.patch.object(
            executor, "run_verify",
            return_value=executor.VerifyResult("passed", 0, "ok\n", 0.3),
        ):
            worker.execute_task(
                self.conn, claimed, ledger_path=self.ledger, runs_root=self.runs,
            )

        self.assertEqual(str(seen["repo"]), str(repo))
        self.assertEqual(seen["prompt"], "make a changelog")
        self.assertEqual(seen["timeout"], worker.DEFAULT_AGENT_TIMEOUT)

    def test_the_agent_gets_its_governed_specialist_contract(self):
        repo = self._make_repo()
        tid = board.create_task(
            self.conn,
            title="research the product",
            prompt="find the unmet need",
            verify_command="python verify.py",
            verify_timeout=60,
            repo=repo,
            agent_role="researcher",
            capabilities=["web_research", "competitor_research"],
        )
        claimed = self._claim(tid)
        seen = {}

        with mock.patch.object(
            executor,
            "run_agent",
            side_effect=lambda repo_arg, prompt, timeout, usage_path=None,
            on_activity=None: (
                seen.update(prompt=prompt),
                self._agent(0),
            )[1],
        ), mock.patch.object(
            executor,
            "run_verify",
            return_value=executor.VerifyResult("passed", 0, "ok\n", 0.3),
        ):
            worker.execute_task(
                self.conn, claimed, ledger_path=self.ledger, runs_root=self.runs,
            )

        self.assertTrue(seen["prompt"].startswith("find the unmet need"))
        self.assertIn("Specialist role: Researcher", seen["prompt"])
        self.assertIn("Do not invent citations", seen["prompt"])
        self.assertIn("marketing-competitor-profiling", seen["prompt"])

    def test_specialist_contract_includes_dynamically_matched_installed_resources(self):
        repo = self._make_repo()
        tid = board.create_task(
            self.conn,
            title="design the interface",
            prompt="build a polished dashboard interface",
            verify_command="python verify.py",
            verify_timeout=60,
            repo=repo,
            capabilities=["taste", "frontend_engineering"],
        )
        claimed = self._claim(tid)
        seen = {}
        resource = worker.capabilities.capability_catalog.CapabilityResource(
            resource_id="skill:ui-ux-pro-max",
            name="ui-ux-pro-max",
            kind="skill",
            origin="installed",
            description="Polished dashboard frontend interface design.",
            path=Path("C:/skills/ui-ux-pro-max/SKILL.md"),
            availability="active",
            instruction_ready=True,
        )

        with mock.patch.object(
            worker.capabilities.capability_catalog, "load_catalog", return_value=[resource]
        ), mock.patch.object(
            executor,
            "run_agent",
            side_effect=lambda repo_arg, prompt, timeout, usage_path=None,
            on_activity=None: (
                seen.update(prompt=prompt),
                self._agent(0),
            )[1],
        ), mock.patch.object(
            executor,
            "run_verify",
            return_value=executor.VerifyResult("passed", 0, "ok\n", 0.3),
        ):
            worker.execute_task(
                self.conn, claimed, ledger_path=self.ledger, runs_root=self.runs,
            )

        self.assertIn("Automatically matched resources", seen["prompt"])
        self.assertIn("skill:ui-ux-pro-max", seen["prompt"])

    # -- verify fail / timeout / spawn-error -----------------------------

    def test_verify_exit_nonzero_reverts_and_returns_to_ready(self):
        repo = self._make_repo()
        tid = self._make_task(repo, title="write changelog", verify="python verify.py")
        claimed = self._claim(tid)

        verify = executor.VerifyResult("failed", 2, "verify found a problem\n", 0.3)
        with self._patch_runners(self._agent(0), verify):
            attempt = worker.execute_task(
                self.conn, claimed, ledger_path=self.ledger, runs_root=self.runs,
            )

        self.assertEqual(attempt.outcome, "failed")
        row = self.task_row(tid)
        self.assertEqual(row["status"], "ready",
                         "a failed verify must return the task to ready (retryable)")
        self.assertIn("verify_failed", self.event_kinds(tid))

        run = self.conn.execute(
            "SELECT outcome, status FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT 1",
            (tid,),
        ).fetchone()
        self.assertEqual(run["outcome"], "failed")
        self.assertEqual(run["status"], "failed")

        entries = ledger.read_entries(self.ledger)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["outcome"], "failed")
        self.assertEqual(entries[0]["verify_exit"], 2)
        self.assertEqual(entries[0]["verify_outcome"], "failed")
        proposal = board.proposal(self.conn, f"task:{tid}:{claimed.current_run_id}:failed")
        self.assertIsNotNone(proposal)
        self.assertEqual((proposal["status"], proposal["suggested_action"]), ("pending", "retry"))

    def test_verify_timeout_is_recorded_without_an_exit_code_and_delayed(self):
        repo = self._make_repo()
        tid = self._make_task(repo, title="write changelog", verify="python verify.py")
        claimed = self._claim(tid)

        verify = executor.VerifyResult("timeout", None, "verify TIMEOUT\n", 3.0)
        with self._patch_runners(self._agent(0), verify):
            attempt = worker.execute_task(
                self.conn, claimed, ledger_path=self.ledger, runs_root=self.runs,
            )

        self.assertEqual(attempt.outcome, "environment_backoff")
        self.assertEqual(self.task_row(tid)["status"], "ready")
        self.assertIn("environment_backoff", self.event_kinds(tid))

        run = self.conn.execute(
            "SELECT outcome, status FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT 1",
            (tid,),
        ).fetchone()
        self.assertEqual(run["outcome"], "timed_out")

        entries = ledger.read_entries(self.ledger)
        self.assertEqual(entries[0]["outcome"], "environment_backoff")
        self.assertIsNone(entries[0]["verify_exit"],
                          "a timeout must not borrow an exit code")
        self.assertEqual(entries[0]["verify_outcome"], "timeout")
        self.assertEqual(entries[0]["failure_class"], "environment")

    def test_verify_spawn_error_is_ledgered_then_delayed(self):
        repo = self._make_repo()
        tid = self._make_task(repo, title="write changelog", verify="missing.exe")
        claimed = self._claim(tid)

        verify = executor.VerifyResult(
            "spawn_error", None, "FileNotFoundError: missing.exe", 0.1,
        )
        with self._patch_runners(self._agent(0), verify):
            attempt = worker.execute_task(
                self.conn, claimed, ledger_path=self.ledger, runs_root=self.runs,
            )

        self.assertEqual(attempt.outcome, "environment_backoff")
        self.assertEqual(self.task_row(tid)["status"], "ready")
        self.assertIn("environment_backoff", self.event_kinds(tid))

        run = self.conn.execute(
            "SELECT outcome FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT 1",
            (tid,),
        ).fetchone()
        self.assertEqual(run["outcome"], "spawn_failed")

        entries = ledger.read_entries(self.ledger)
        self.assertEqual(entries[0]["outcome"], "environment_backoff")
        self.assertIsNone(entries[0]["verify_exit"])
        self.assertEqual(entries[0]["verify_outcome"], "spawn_error")
        self.assertEqual(entries[0]["failure_class"], "environment")

    # -- the upstream-artifact gate runs before the agent -----------------

    def _make_parent_child(self, repo: Path, *, artifact_present: bool):
        (repo / "out").mkdir(exist_ok=True)
        if artifact_present:
            (repo / "out" / "report.json").write_text('{"ok":true}', encoding="utf-8")
            self._commit(repo, "artifacts")
        parent = board.create_task(
            self.conn, title="upstream", prompt="produce out/report.json",
            verify_command="true", repo=repo, verify_timeout=60,
            expected_artifacts=["out/report.json"],
        )
        # The child is only claimable once its parent is done; complete the
        # parent so the child reaches `ready`.
        self.assertTrue(self.kb.complete_task(self.conn, parent, result="verified"))
        child = self._make_task(repo, title="downstream", verify="python check.py",
                                parents=[parent])
        return child

    def test_verify_exit_zero_with_upstream_artifacts_present_completes(self):
        """The full happy path: upstream gate passes, verify passes -> done."""
        repo = self._make_repo()
        child = self._make_parent_child(repo, artifact_present=True)
        claimed = self._claim(child)

        verify = executor.VerifyResult("passed", 0, "verify ok\n", 0.3)
        with mock.patch.object(
            executor, "run_agent", return_value=self._agent(0),
        ) as ra, mock.patch.object(executor, "run_verify", return_value=verify):
            attempt = worker.execute_task(
                self.conn, claimed, ledger_path=self.ledger, runs_root=self.runs,
            )

        self.assertEqual(attempt.outcome, "passed")
        self.assertEqual(self.task_row(child)["status"], "done")
        self.assertEqual(ra.call_count, 1)
        self.assertEqual(ledger.read_entries(self.ledger)[0]["outcome"], "passed")

    def test_missing_upstream_artifact_never_invokes_the_agent(self):
        repo = self._make_repo()
        child = self._make_parent_child(repo, artifact_present=False)
        claimed = self._claim(child)

        def _boom(*a, **k):  # the gate failing means neither may run
            raise AssertionError(
                "run_agent was invoked although the upstream gate failed"
            )
        with mock.patch.object(executor, "run_agent", side_effect=_boom) as ra, \
             mock.patch.object(executor, "run_verify", side_effect=_boom) as rv:
            attempt = worker.execute_task(
                self.conn, claimed, ledger_path=self.ledger, runs_root=self.runs,
            )

        self.assertEqual(attempt.outcome, "failed")
        self.assertEqual(ra.call_count, 0)
        self.assertEqual(rv.call_count, 0)
        self.assertEqual(self.task_row(child)["status"], "ready")
        self.assertIn("verify_failed", self.event_kinds(child))

        entries = ledger.read_entries(self.ledger)
        self.assertEqual(entries[0]["outcome"], "failed")
        self.assertIn("missing artifact", entries[0]["reason"])
        self.assertIsNone(entries[0]["verify_outcome"])
        self.assertIsNone(entries[0]["verify_exit"])

    # -- one task per invocation ------------------------------------------

    def test_run_once_executes_exactly_one_task(self):
        repo = self._make_repo()
        a = self._make_task(repo, title="task a", verify="python a.py")
        b = self._make_task(repo, title="task b", verify="python b.py")
        # Both leave the agent's verdict at the door; whichever is picked, only
        # one may be processed by a single invocation.
        with mock.patch.object(
            executor, "run_agent", return_value=self._agent(0),
        ), mock.patch.object(
            executor, "run_verify",
            return_value=executor.VerifyResult("passed", 0, "ok\n", 0.3),
        ):
            attempt = worker.run_once(
                self.conn, ledger_path=self.ledger, runs_root=self.runs,
            )

        self.assertIsNotNone(attempt)
        self.assertEqual(attempt.outcome, "passed")
        self.assertEqual(
            len(ledger.read_entries(self.ledger)), 1,
            "one invocation may record exactly one attempt",
        )
        statuses = [self.task_row(t)["status"] for t in (a, b)]
        self.assertEqual(sorted(statuses), ["done", "ready"],
                         f"exactly one task may leave the invocation as done, got {statuses}")

    def test_failed_task_retries_once_then_the_kernel_circuit_breaker_blocks_it(self):
        """Known-state failures are retryable, but never an unbounded loop."""
        repo = self._make_repo()
        tid = self._make_task(repo, title="known bad task", verify="python check.py")
        failed = executor.VerifyResult("failed", 2, "not acceptable\n", 0.3)

        with self._patch_runners(self._agent(0), failed) as (ra, rv):
            summary = worker.run(
                self.conn, max_tasks=3, ledger_path=self.ledger, runs_root=self.runs,
            )

        self.assertEqual(summary.outcomes, ["failed", "failed"])
        self.assertEqual(self.task_row(tid)["status"], "blocked")
        self.assertIn("gave_up", self.event_kinds(tid))
        self.assertEqual(ra.call_count, 2)
        self.assertEqual(rv.call_count, 2)
        self.assertEqual(len(ledger.read_entries(self.ledger)), 2)


if __name__ == "__main__":
    unittest.main()
