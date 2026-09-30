"""A retry should know why the last attempt was rejected.

Task `t_627c9496`, sent from a phone on 2026-09-29, failed twice in a row:

    run 129  rejected: the page keeps 0 of its own 5 promises
    run 130  rejected: no image, svg or background-image anywhere

Run 130 had no idea run 129 had happened. Each retry starts a fresh agent
that redoes the research and makes a sibling mistake, and the board records
only "verify exit 1" - the reasons live in each run's `verify.log` and were
read by nobody.

The prompt already tells the agent to check its own promises before
stopping ("LAST THING BEFORE YOU STOP: search your finished page for each
bullet you wrote"). It ignored that rule, so adding more prompt text is
not the fix. What was missing is *evidence from its own last attempt*.

This is deliberately narrow. It carries the verifier's words, not advice
invented about them, because the verifier is the thing that decides and
anything else would be a second opinion competing with the gate.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import taste  # noqa: E402
import worker  # noqa: E402


REAL_LOG = """verify: index.html parses (17579 bytes)
verify: this run produced BRIEF.md
verify: this run produced index.html
verify: 2 deliverable(s) from this run
verify: this does not look like it was made for its subject:
  no image, svg or background-image anywhere
  promised but not visible on the page: 'ClinVar'
  promised but not visible on the page: 'GRCh38'
"""

PASSING_LOG = """verify: shop.html parses (18270 bytes)
verify: 2 deliverable(s) from this run
verify: kept 6 of 6 promise(s) from 3 reference(s)
verify: deliverables archived, tree clean for the next task
"""


class ReasonsTest(unittest.TestCase):
    def test_it_keeps_the_verifier_s_own_words(self):
        reasons = taste.rejection_reasons(REAL_LOG)
        self.assertIn("no image, svg or background-image anywhere", reasons)
        self.assertIn("promised but not visible on the page: 'ClinVar'", reasons)

    def test_it_drops_the_lines_that_are_not_complaints(self):
        reasons = taste.rejection_reasons(REAL_LOG)
        joined = " ".join(reasons)
        self.assertNotIn("parses", joined)
        self.assertNotIn("deliverable(s) from this run", joined)

    def test_a_passing_log_yields_nothing(self):
        self.assertEqual(taste.rejection_reasons(PASSING_LOG), ())

    def test_empty_input_is_not_an_error(self):
        self.assertEqual(taste.rejection_reasons(""), ())
        self.assertEqual(taste.rejection_reasons(None), ())


class BlockTest(unittest.TestCase):
    def test_no_reasons_means_no_block(self):
        """A first attempt must read exactly as it does today."""
        self.assertEqual(taste.rejection_block(()), "")

    def test_the_block_carries_the_reasons_verbatim(self):
        block = taste.rejection_block(("no image, svg or background-image anywhere",))
        self.assertIn("no image, svg or background-image anywhere", block)

    def test_it_says_the_words_are_the_verifier_s(self):
        """So the agent treats them as the gate's verdict rather than as a
        suggestion it may reason its way out of."""
        block = taste.rejection_block(("x",))
        self.assertIn("verifier", block.lower())

    def test_it_does_not_invent_instructions_beyond_the_reasons(self):
        block = taste.rejection_block(("promised but not visible: 'ClinVar'",))
        self.assertNotIn("you should probably", block.lower())
        self.assertLess(len(block), 700, "a long lecture drowns the task itself")


class FindingThePreviousLogTest(unittest.TestCase):
    def _runs(self, attempts):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        for name, text in attempts.items():
            folder = root / "t_x" / str(name)
            folder.mkdir(parents=True)
            if text is not None:
                (folder / "verify.log").write_text(text, encoding="utf-8")
        return root

    def test_it_reads_the_most_recent_earlier_attempt(self):
        root = self._runs({129: "verify: this does not look like it:\n  old\n",
                           130: REAL_LOG})
        reasons = worker.previous_rejection(root, "t_x", current_run_id=131)
        self.assertIn("no image, svg or background-image anywhere", reasons)

    def test_it_ignores_the_current_run(self):
        """The current attempt's own log does not exist yet, and a stale one
        from a re-used id would describe work this agent has not done."""
        root = self._runs({130: REAL_LOG})
        self.assertEqual(worker.previous_rejection(root, "t_x", current_run_id=130), ())

    def test_a_first_attempt_has_nothing_to_report(self):
        self.assertEqual(
            worker.previous_rejection(self._runs({}), "t_x", current_run_id=1), ())

    def test_a_missing_runs_directory_is_not_an_error(self):
        self.assertEqual(
            worker.previous_rejection(Path("C:/nowhere"), "t_x", current_run_id=2), ())

    def test_an_attempt_with_no_verify_log_is_skipped(self):
        root = self._runs({129: REAL_LOG, 130: None})
        self.assertIn("no image, svg or background-image anywhere",
                      worker.previous_rejection(root, "t_x", current_run_id=131))

    def test_non_numeric_attempt_folders_do_not_crash_it(self):
        root = self._runs({129: REAL_LOG, "scratch": "verify: junk\n"})
        worker.previous_rejection(root, "t_x", current_run_id=200)


class ItReachesTheAgentTest(unittest.TestCase):
    """A feedback loop nobody calls is the sixth dead module."""

    def test_the_worker_appends_it_to_the_prompt(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "worker.py"
                  ).read_text(encoding="utf-8")
        self.assertIn("previous_rejection(", source)
        self.assertIn("rejection_block(", source)

    def test_it_is_added_before_the_agent_runs_not_after(self):
        """Feedback gathered after the agent has already been launched
        would reach nobody. `rindex` because worker.py's module docstring
        mentions run_agent as an example long before the real call."""
        source = (Path(__file__).resolve().parents[1] / "src" / "worker.py"
                  ).read_text(encoding="utf-8")
        self.assertLess(source.index("rejection_block("),
                        source.rindex("executor.run_agent("))


if __name__ == "__main__":
    unittest.main()
