"""`lane_select` reads `agent_role`. Nothing writes it.

`lane_select._outcomes` filters ledger rows with

    if row.get("agent_role") != role: continue

and `_entry` never emits that field. Confirmed against the live ledger: the
keys are agent_exit, agent_log, branch, failure_class, model, model_source,
outcome, provider, reason, repo, run_id, seconds, task_id, title, ts,
verify_exit, verify_log, verify_outcome, worker. No `agent_role`.

So `_outcomes` returns `{}` for every role, and the measured-outcome override
in `choose()` - the thing that makes lane selection learn from verify results
rather than transport results, which is the whole argument for having it -
has never been able to fire. Dead code inside a module that is itself
imported by nothing.

These tests use a ledger written by a real worker attempt rather than
hand-made rows, because a hand-made row is exactly what hid this: every
existing `lane_select` test builds its own ledger lines and so agrees with
the consumer about a field the producer does not write.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import BoardTestCase  # noqa: E402

import board  # noqa: E402
import executor  # noqa: E402
import lane_select  # noqa: E402
import ledger as ledger_module  # noqa: E402
import worker  # noqa: E402


class LedgerFeedsLaneSelectTest(BoardTestCase):
    ROLE = "frontend_builder"

    def setUp(self) -> None:
        super().setUp()
        self.ledger = self.tmp / "ledger.jsonl"
        self.runs = self.tmp / "runs"
        self.repo = self.tmp / "workspace"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"],
                       cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=self.repo, check=True)
        (self.repo / "marker.txt").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"],
                       cwd=self.repo, check=True)

    def _attempt(self, *, model, passing, api_calls=6):
        """One real worker attempt, agent and verify doubled."""
        task_id = board.create_task(
            self.conn,
            title="Build the hero section",
            prompt="build the hero section",
            verify_command="python -c \"pass\"",
            repo=self.repo,
            verify_timeout=60,
            agent_role=self.ROLE,
        )
        agent = executor.AgentResult(
            0, "done\n", 1.0, model=model, provider="p",
            model_source="usage_file", api_calls=api_calls)
        verify = executor.VerifyResult(
            "passed" if passing else "failed", 0 if passing else 1, "out", 0.1)
        claimed = self.kb.claim_task(self.conn, task_id,
                                     claimer=worker.worker_id())
        with mock.patch.object(executor, "run_agent", return_value=agent), \
             mock.patch.object(executor, "run_verify", return_value=verify):
            worker.execute_task(self.conn, claimed, ledger_path=self.ledger,
                                runs_root=self.runs)
        return task_id

    def test_a_real_attempt_records_the_role(self):
        self._attempt(model="deskollama/qwen2.5-coder:7b", passing=True)
        entry = ledger_module.read_entries(self.ledger)[-1]
        self.assertEqual(entry["agent_role"], self.ROLE)

    def test_lane_select_can_see_a_real_run(self):
        """The join that has never worked."""
        self._attempt(model="deskollama/qwen2.5-coder:7b", passing=True)
        outcomes = lane_select._outcomes(self.ledger, self.ROLE)
        self.assertEqual(outcomes.get("deskollama/qwen2.5-coder:7b"), (1, 1))

    def test_passes_and_failures_are_counted_apart(self):
        for _ in range(3):
            self._attempt(model="lane-a", passing=True)
        self._attempt(model="lane-a", passing=False)
        self._attempt(model="lane-b", passing=False)
        outcomes = lane_select._outcomes(self.ledger, self.ROLE)
        self.assertEqual(outcomes.get("lane-a"), (3, 4))
        self.assertEqual(outcomes.get("lane-b"), (0, 1))

    def test_another_role_is_not_counted(self):
        """The filter has to actually filter, not just stop discarding
        everything."""
        self._attempt(model="lane-a", passing=True)
        self.assertEqual(lane_select._outcomes(self.ledger, "designer"), {})

    def test_the_default_role_is_recorded_rather_than_left_blank(self):
        """`board.verify_spec` substitutes 'builder' for a task with no role,
        so the ledger must record what the run was actually held to."""
        task_id = board.create_task(
            self.conn, title="x", prompt="x",
            verify_command="python -c \"pass\"", repo=self.repo,
            verify_timeout=60,
        )
        agent = executor.AgentResult(0, "done\n", 1.0, model="lane-a",
                                     model_source="usage_file", api_calls=4)
        verify = executor.VerifyResult("passed", 0, "out", 0.1)
        claimed = self.kb.claim_task(self.conn, task_id,
                                     claimer=worker.worker_id())
        with mock.patch.object(executor, "run_agent", return_value=agent), \
             mock.patch.object(executor, "run_verify", return_value=verify):
            worker.execute_task(self.conn, claimed, ledger_path=self.ledger,
                                runs_root=self.runs)
        self.assertEqual(
            ledger_module.read_entries(self.ledger)[-1]["agent_role"], "builder")


if __name__ == "__main__":
    unittest.main()
