"""The scaffolding around an agent's prompt must be satisfiable.

Task `t_c9b08613` arrived from Telegram: *"Build me a landing page for a brand
called Mishwan..."*. It named no files, and the rules told the agent:

    Touch ONLY the files this task names.

So the agent asked which files it was allowed to create - the right instinct
against that rule, and a failed run, because a one-shot turn has nobody to
answer. The instruction penalised exactly the open-ended requests the Telegram
intake exists to accept.

These tests pin the properties that failure taught, not the wording that
happens to carry them today.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import executor  # noqa: E402


class TheRulesAreSatisfiable(unittest.TestCase):
    def rules(self) -> str:
        # Whitespace-collapsed: these assertions are about what the rules say,
        # not where the lines happen to wrap. A test that breaks on reflowing a
        # paragraph tests the formatter, not the instruction.
        return " ".join(executor.HARD_RULES.lower().split())

    def test_no_unconditional_demand_for_files_the_task_may_not_name(self):
        # The exact sentence that failed t_c9b08613.
        self.assertNotIn(
            "touch only the files this task names", self.rules(),
            "an unconditional rule about named files is unsatisfiable for a "
            "request that names none, and makes a careful agent ask a question "
            "nobody can answer",
        )

    def test_it_says_what_to_do_when_no_files_are_named(self):
        rules = self.rules()
        self.assertTrue(
            "if it does not" in rules or "does not" in rules,
            "the rules must cover the case where the task names no files",
        )
        self.assertIn(
            "create whatever files the work needs", rules,
            "an agent given an open-ended request needs explicit permission to "
            "create files",
        )

    def test_it_says_nobody_is_listening(self):
        rules = self.rules()
        self.assertIn("nobody can answer you", rules)
        self.assertIn("unattended", rules)

    def test_it_says_a_question_is_a_failure(self):
        self.assertIn("failed run", self.rules())

    def test_it_still_forbids_what_it_always_forbade(self):
        rules = self.rules()
        for forbidden in ("git push", "git merge", "git rebase", "deploy"):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, rules)
        self.assertIn(".env", rules)
        self.assertIn("credentials", rules)

    def test_it_still_refuses_a_half_finished_edit(self):
        self.assertIn("half-finished edit", self.rules())

    def test_it_says_the_file_is_the_deliverable(self):
        # The other failure mode all night: a model describing the file it was
        # asked to write. The rules now say which one counts.
        self.assertIn("description of a file is not a file", self.rules())


class ThePromptCarriesTheWorkspace(unittest.TestCase):
    """The cd preamble is load-bearing and must survive any rewording."""

    def test_the_repo_path_reaches_the_agent(self):
        built = executor.build_prompt(r"C:\somewhere\workspace", "do the thing")
        self.assertIn(r"C:\somewhere\workspace", built)

    def test_the_task_survives_intact(self):
        task = "Build me a landing page for a brand called Mishwan"
        self.assertIn(task, executor.build_prompt(Path.cwd(), task))

    def test_the_rules_are_appended(self):
        built = executor.build_prompt(Path.cwd(), "x")
        self.assertIn("NON-NEGOTIABLE", built)


if __name__ == "__main__":
    unittest.main()
