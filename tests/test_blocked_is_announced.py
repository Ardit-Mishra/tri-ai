"""A task that has given up must say so.

The circuit breaker trips at `DEFAULT_FAILURE_LIMIT` (2): the task becomes
`blocked` and is never claimed again. Nothing tells the operator. They get two
FAILED cards that read exactly like the first failure of a task that will be
retried, and then silence.

That is not hypothetical. In the eleven-node fan-out three designers hit the
limit and seven builders were stranded behind them for ever, and it was found
by querying the board directly - there was no message. It is also the
concrete shape of "the experience of interacting with it was not great":
work stops and the system says nothing.

`pending_completions_for_chat` already selects `t.status` alongside the run,
so the card can see the task gave up without reading anything new.

The card also now has something to say about *why*. `answered_without_tools`
is on the run's metadata, and a task whose attempts were all one-call turns
is not a task that needs a better prompt - it is a lane that cannot drive the
tool loop, which is a different action for the operator to take.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import completion_report  # noqa: E402


def _card(*, status="failed", outcome="failed", **meta):
    return completion_report.render({
        "task_id": "t_x", "run_id": 4, "outcome": outcome, "status": status,
        "title": "Design the checkout page",
        "metadata": json.dumps({"verify_exit": 1, **meta}),
        "artifacts": (),
    }).text


class ABlockedTaskSaysSoTest(unittest.TestCase):
    def test_a_blocked_task_is_named_as_given_up(self):
        text = _card(status="blocked").casefold()
        self.assertIn("blocked", text)

    def test_it_says_the_work_will_not_be_retried(self):
        """The operator's actual question is "is it still going?"."""
        text = _card(status="blocked").casefold()
        self.assertIn("retr", text)

    def test_an_ordinary_failure_is_not_announced_as_blocked(self):
        """One failure of two is still in flight; saying otherwise would be
        the same overstatement in the other direction."""
        self.assertNotIn("blocked", _card(status="failed").casefold())

    def test_a_completed_task_says_nothing_about_blocking(self):
        text = _card(status="done", outcome="completed").casefold()
        self.assertNotIn("blocked", text)

    def test_a_missing_status_is_not_treated_as_blocked(self):
        text = completion_report.render({
            "task_id": "t_x", "run_id": 4, "outcome": "failed",
            "title": "t", "metadata": "{}", "artifacts": (),
        }).text
        self.assertNotIn("blocked", text.casefold())


class ItSaysWhichKindOfProblemTest(unittest.TestCase):
    """A blocked task is one decision, and the decision differs by cause."""

    def test_a_silent_turn_points_at_the_lane_not_the_task(self):
        text = _card(status="blocked", answered_without_tools=True,
                     model="devstral:24b").casefold()
        self.assertIn("lane", text)

    def test_a_working_turn_does_not_blame_the_lane(self):
        text = _card(status="blocked", answered_without_tools=False).casefold()
        self.assertNotIn("lane", text)

    def test_an_unknown_count_blames_nothing(self):
        self.assertNotIn("lane", _card(status="blocked").casefold())

    def test_the_lane_note_needs_the_task_to_be_blocked(self):
        """Mid-flight, one silent turn is a run problem, not yet a verdict."""
        text = _card(status="failed", answered_without_tools=True).casefold()
        self.assertNotIn("lane", text)



# --- and again against the real board ---------------------------------------
#
# The class above builds the completion row itself, so it can only prove the
# renderer. Twice this session a producer wrote a field the consumer never
# read while every hand-made-fixture test stayed green, so the query that
# actually feeds the card has to be exercised too: `t.status` must be the
# task's status at the moment the card is built, and the breaker must really
# have tripped rather than the test asserting that it did.

import subprocess  # noqa: E402
from unittest import mock  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import BoardTestCase  # noqa: E402

import board  # noqa: E402
import executor  # noqa: E402
import worker  # noqa: E402


class TheRealCardSaysBlockedTest(BoardTestCase):
    CHAT = "42"

    def setUp(self) -> None:
        super().setUp()
        self.ledger = self.tmp / "ledger.jsonl"
        self.runs = self.tmp / "runs"
        self.repo = self.tmp / "workspace"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "config", "user.email", "t@e.com"],
                       cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=self.repo, check=True)
        (self.repo / "m.txt").write_text("b\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.repo, check=True)

    def _attempt(self, task_id, *, api_calls):
        agent = executor.AgentResult(
            0, "I'll build that for you.\n", 1.0, model="devstral:24b",
            model_source="usage_file", api_calls=api_calls)
        verify = executor.VerifyResult("failed", 1, "verify: nothing", 0.1)
        claimed = self.kb.claim_task(self.conn, task_id,
                                     claimer=worker.worker_id())
        self.assertIsNotNone(claimed, "task was not claimable")
        with mock.patch.object(executor, "run_agent", return_value=agent), \
             mock.patch.object(executor, "run_verify", return_value=verify):
            worker.execute_task(self.conn, claimed, ledger_path=self.ledger,
                                runs_root=self.runs)

    def _drive_to_blocked(self, *, api_calls):
        task_id = board.create_task(
            self.conn, title="Design the checkout page",
            prompt="design the checkout page",
            verify_command="python -c \"import sys; sys.exit(1)\"",
            repo=self.repo, verify_timeout=60, agent_role="designer")
        for _ in range(2):          # DEFAULT_FAILURE_LIMIT
            self.kb.recompute_ready(self.conn)
            row = self.conn.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row["status"] == "blocked":
                break
            self._attempt(task_id, api_calls=api_calls)
        return task_id

    def _cards(self):
        return [completion_report.render(row, runs_root=self.runs).text
                for row in board.pending_completions_for_chat(
                    self.conn, self.CHAT)]

    def test_the_breaker_really_trips(self):
        """Guard the guard: if the task is not blocked the rest proves nothing."""
        task_id = self._drive_to_blocked(api_calls=1)
        status = self.conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()[0]
        self.assertEqual(status, "blocked")

    def test_the_operator_is_told(self):
        self._drive_to_blocked(api_calls=1)
        self.assertIn("blocked", "\n".join(self._cards()).casefold())

    def test_the_lane_is_named_as_the_likely_cause(self):
        self._drive_to_blocked(api_calls=1)
        self.assertIn("lane", "\n".join(self._cards()).casefold())

    def test_a_task_that_used_tools_is_not_blamed_on_the_lane(self):
        self._drive_to_blocked(api_calls=9)
        cards = "\n".join(self._cards()).casefold()
        self.assertIn("blocked", cards)
        self.assertNotIn("looks like the lane", cards)

if __name__ == "__main__":
    unittest.main()
