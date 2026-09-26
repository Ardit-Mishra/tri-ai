"""Separate "the agent tried and failed" from "the agent never used a tool".

Both look identical today: an unchanged workspace, a failed gate, one attempt
against the circuit breaker. The ledger records `outcome: failed` and nothing
else, so the most common single failure in this system has been invisible for
82 runs.

Measured over the desktop's whole ledger on 2026-09-26 — 82 entries, 74 with
a usage file:

    api_calls <= 1    passed  1/21    failed  19/52
    devstral:24b      10 of its 11 runs made exactly one call, 1 pass
    auto/best-free     0 of its  7 runs did,                   7 passes

Nineteen of fifty-two failures are a model that answered once, in prose, and
never called the terminal tool — it wrote the shell command, or the HTML
itself, into its reply. Reading those logs they look like "produced nothing",
which is why they were first mistaken for refusals; only two of the nineteen
are refusal-shaped.

`api_calls` is the signal, and it needs no text heuristics: the runtime writes
it to `usage.json` itself. This does **not** change control flow. A one-call
run still fails and still counts toward the breaker — exempting it would loop
a model that cannot drive the tool loop for ever. It is recorded, so the lane
selector has something real to select on and so a card can say what happened.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import executor  # noqa: E402
import worker  # noqa: E402


def _usage(**fields) -> Path:
    path = Path(tempfile.mkdtemp(prefix="triai-usage-")) / "usage.json"
    path.write_text(json.dumps(fields), encoding="utf-8")
    return path


class UsageCarriesApiCallsTest(unittest.TestCase):
    """`AgentResult` must surface the count, not just the model."""

    def test_the_count_is_read_off_the_usage_file(self):
        result = executor.read_usage(_usage(
            model="devstral:24b", provider="ollama", api_calls=1,
            completed=True, failed=False))
        self.assertEqual(result.api_calls, 1)

    def test_a_usage_file_without_the_field_reports_none(self):
        """Absence of a record is not a record of silence."""
        result = executor.read_usage(_usage(model="x", completed=True))
        self.assertIsNone(result.api_calls)

    def test_a_missing_usage_file_reports_none(self):
        result = executor.read_usage(Path(tempfile.gettempdir()) / "nope.json")
        self.assertIsNone(result.api_calls)

    def test_a_corrupt_usage_file_does_not_raise(self):
        path = Path(tempfile.mkdtemp(prefix="triai-usage-")) / "usage.json"
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(executor.read_usage(path).api_calls)


class SilentTurnTest(unittest.TestCase):
    """One call means the model answered and stopped."""

    def test_one_call_is_a_turn_with_no_tool_use(self):
        self.assertTrue(executor.answered_without_tools(1))

    def test_zero_calls_counts_too(self):
        self.assertTrue(executor.answered_without_tools(0))

    def test_two_calls_means_something_came_back_from_a_tool(self):
        self.assertFalse(executor.answered_without_tools(2))

    def test_an_unknown_count_is_not_an_accusation(self):
        """A runtime that writes no count must not be reported as silent."""
        self.assertIsNone(executor.answered_without_tools(None))


class LedgerCarriesItTest(unittest.TestCase):
    """The whole point: it has to be countable afterwards."""

    class _Claimed:
        id = "t_x"
        title = "Build the thing"

    def _entry(self, **kw):
        return worker._entry(
            self._Claimed(), run_id=7, repo="/tmp/x", branch=None,
            outcome="failed", **kw)

    def test_the_call_count_is_recorded(self):
        entry = self._entry(api_calls=1)
        self.assertEqual(entry["api_calls"], 1)

    def test_a_silent_turn_is_named_in_the_ledger(self):
        self.assertIs(self._entry(api_calls=1)["answered_without_tools"], True)

    def test_a_working_turn_is_named_too(self):
        self.assertIs(self._entry(api_calls=9)["answered_without_tools"], False)

    def test_an_unknown_count_leaves_both_fields_null(self):
        entry = self._entry()
        self.assertIsNone(entry["api_calls"])
        self.assertIsNone(entry["answered_without_tools"])

    def test_every_existing_field_survives(self):
        """The ledger is append-only and read by the dashboard and reports."""
        entry = self._entry(api_calls=3, model="devstral:24b", seconds=12.5)
        for field in ("ts", "worker", "task_id", "run_id", "title", "repo",
                      "branch", "outcome", "verify_exit", "verify_outcome",
                      "agent_exit", "model", "provider", "model_source",
                      "seconds", "reason", "failure_class", "agent_log",
                      "verify_log"):
            self.assertIn(field, entry)


if __name__ == "__main__":
    unittest.main()


class TheCardSaysWhatHappenedTest(unittest.TestCase):
    """"produced no files in the workspace" is true and useless.

    It is the same sentence whether the agent worked for twenty minutes and
    got it wrong, or answered in one sentence and never touched the disk.
    The second was 19 of 52 failures and nothing on the card distinguished
    them, which is why it went unnoticed for 82 runs.
    """

    import completion_report  # noqa: E402  (module under test)

    def _card(self, **meta):
        # `metadata` is a JSON string on the board row, not a dict.
        return self.completion_report.render({
            "task_id": "t_x", "run_id": 7, "outcome": "failed",
            "title": "Build a recipe card page",
            "metadata": json.dumps({"verify_exit": 1, **meta}),
            "artifacts": (),
        }).text

    def test_a_silent_turn_is_named(self):
        text = self._card(answered_without_tools=True, model="devstral:24b")
        self.assertIn("produced no files", text)
        self.assertIn("never used a tool", text.casefold())

    def test_a_turn_that_used_tools_is_not_accused(self):
        text = self._card(answered_without_tools=False)
        self.assertNotIn("never used a tool", text.casefold())

    def test_an_unknown_count_says_nothing_either_way(self):
        self.assertNotIn("never used a tool", self._card().casefold())

    def test_the_model_is_still_named_so_the_lane_is_visible(self):
        """Ten of devstral:24b's eleven runs did this. Name it on the card."""
        self.assertIn("devstral:24b",
                      self._card(answered_without_tools=True,
                                 model="devstral:24b"))
