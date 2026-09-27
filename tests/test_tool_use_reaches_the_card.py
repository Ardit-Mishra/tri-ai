"""End to end: a silent turn must reach the operator's card.

`test_tool_use_signal.py` builds the completion row by hand and checks the
renderer. That proves the renderer and nothing else — the same model wrote
the producer, the consumer and the fixture, and a hand-made row cannot catch
the producer writing to a column the consumer never reads.

The signal crosses four boundaries before anyone sees it:

    usage.json -> AgentResult.api_calls -> _board_failure metadata
              -> task_runs.metadata -> pending_completions_for_chat
              -> completion_report.render

`_board_failure` writes the *run's* metadata through `kb._end_run`, and
`pending_completions_for_chat` selects `r.metadata` from `task_runs`. Those
have to be the same column. This runs a real worker attempt against a real
board and a real git repo, with only the agent and the verify command
doubled, and reads the card back the way Telegram does.
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
import completion_report  # noqa: E402
import executor  # noqa: E402
import worker  # noqa: E402


class SilentTurnReachesTheCardTest(BoardTestCase):
    CHAT = "42"

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

    def _run_a_failing_attempt(self, *, api_calls):
        """One whole worker attempt whose agent produced nothing."""
        task_id = board.create_task(
            self.conn,
            title="Build a recipe card page",
            prompt="build a recipe card page",
            verify_command="python -c \"import sys; sys.exit(1)\"",
            repo=self.repo,
            verify_timeout=60,
        )
        # No subscription step: `pending_completions_for_chat` returns every
        # finished run this chat has not yet been sent a receipt for.
        agent = executor.AgentResult(
            0, "I'll create the recipe card page for you.\n", 1.2,
            model="devstral:24b", provider="ollama",
            model_source="usage_file", api_calls=api_calls,
        )
        verify = executor.VerifyResult("failed", 1, "verify: nothing changed", 0.2)
        claimed = self.kb.claim_task(self.conn, task_id,
                                     claimer=worker.worker_id())
        self.assertIsNotNone(claimed)
        with mock.patch.object(executor, "run_agent", return_value=agent), \
             mock.patch.object(executor, "run_verify", return_value=verify):
            worker.execute_task(self.conn, claimed, ledger_path=self.ledger,
                                runs_root=self.runs)
        return task_id

    def _cards(self):
        return [completion_report.render(row, runs_root=self.runs).text
                for row in board.pending_completions_for_chat(
                    self.conn, self.CHAT)]

    def test_the_card_says_the_model_never_used_a_tool(self):
        self._run_a_failing_attempt(api_calls=1)
        cards = self._cards()
        self.assertTrue(cards, "no completion card was produced at all")
        self.assertIn("never used a tool", "\n".join(cards).casefold())

    def test_a_turn_that_used_tools_is_not_accused_on_the_card(self):
        self._run_a_failing_attempt(api_calls=11)
        self.assertNotIn("never used a tool", "\n".join(self._cards()).casefold())

    def test_an_unknown_count_says_nothing_on_the_card(self):
        self._run_a_failing_attempt(api_calls=None)
        self.assertNotIn("never used a tool", "\n".join(self._cards()).casefold())

    def test_the_ledger_agrees_with_the_card(self):
        """Two consumers, one fact. They must not be able to disagree."""
        import ledger as ledger_module
        self._run_a_failing_attempt(api_calls=1)
        entries = ledger_module.read_entries(self.ledger)
        self.assertTrue(entries)
        self.assertEqual(entries[-1]["api_calls"], 1)
        self.assertIs(entries[-1]["answered_without_tools"], True)
        self.assertIn("never used a tool", "\n".join(self._cards()).casefold())


if __name__ == "__main__":
    unittest.main()


class TheOtherTwoSignalsReachTheCardTest(SilentTurnReachesTheCardTest):
    """Found by Codex reviewing this change set, and it was right twice.

    `head_moved` and `model_requested` were recorded in the ledger and written
    into the retained `verify.log`, and stopped there. The completion card
    reads the *run's* metadata, and `_board_failure` wrote neither - so the
    operator, who sees the card and not the log, got the generic rejected-run
    message in both cases:

      * the agent committed its work, the verifier saw a clean tree and
        failed it, and the card said "produced no files in the workspace";
      * the task pinned a lane, something else answered, and the card named
        only the lane that answered - so the run reads as evidence about a
        lane that never served it.

    Exactly the producer/consumer gap the review was asked to hunt, in code
    written to close that gap elsewhere. `test_head_moved.py` and
    `test_model_override.py` assert the ledger and the log and stop where the
    production code stopped, which is how same-author tests miss this.
    """

    def _run(self, *, head_moved=None, requested=None, served="auto/best-coding"):
        task_id = board.create_task(
            self.conn, title="Build a page", prompt="build a page",
            verify_command="python -c \"import sys; sys.exit(1)\"",
            repo=self.repo, verify_timeout=60, agent_role="builder")
        agent = executor.AgentResult(
            0, "done\n", 1.0, model=served, provider="p",
            model_source="usage_file", api_calls=7,
            head_moved=head_moved, model_requested=requested)
        verify = executor.VerifyResult("failed", 1, "verify: nothing", 0.1)
        claimed = self.kb.claim_task(self.conn, task_id,
                                     claimer=worker.worker_id())
        with mock.patch.object(executor, "run_agent", return_value=agent), \
             mock.patch.object(executor, "run_verify", return_value=verify):
            worker.execute_task(self.conn, claimed, ledger_path=self.ledger,
                                runs_root=self.runs)
        return "\n".join(self._cards())

    def test_a_commit_during_the_turn_is_on_the_card(self):
        self.assertIn("committed", self._run(head_moved=True).casefold())

    def test_an_ordinary_turn_says_nothing_about_committing(self):
        self.assertNotIn("committed", self._run(head_moved=False).casefold())

    def test_an_ignored_lane_override_is_on_the_card(self):
        text = self._run(requested="auto/best-free", served="auto/smart")
        self.assertIn("auto/best-free", text)
        self.assertIn("auto/smart", text)

    def test_an_honoured_override_is_not_flagged(self):
        text = self._run(requested="auto/smart", served="auto/smart")
        self.assertNotIn("not honoured", text.casefold())

    def test_no_override_is_not_flagged(self):
        self.assertNotIn("not honoured", self._run().casefold())
