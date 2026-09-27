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

import os
import sys
import unittest
from unittest import mock
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


class TheAgentIsToldWhereWorkGoesAndWhatNotToRun(unittest.TestCase):
    """Two rules earned by one stress-test task, t_7fbf6644.

    Run 59 finished its work and then ran the verifier itself. verify.py moves
    a run's output into `runs/<slug>/` and commits it, so by the time the
    worker's own verify executed the workspace was clean and the run was
    recorded as having produced nothing at all. The agent had done the job; the
    evidence was gone because it had filed it.

    Run 60 wrote its files to the workspace and its BRIEF.md to the home
    directory, and was rejected for a missing brief that existed one directory
    away.

    Neither is the agent being careless. Both are instructions that could be
    read another way, on an unattended turn, with nobody to ask.
    """

    def rules(self) -> str:
        return " ".join(executor.HARD_RULES.split()).lower()

    def test_the_verifier_is_off_limits(self) -> None:
        rules = self.rules()
        self.assertIn("never run the verifier", rules)
        self.assertIn("verify.py", rules)

    def test_it_says_why_running_the_verifier_destroys_the_run(self) -> None:
        # A bare prohibition invites working around it. The reason is the rule.
        rules = self.rules()
        self.assertIn("archives and commits", rules)
        self.assertIn("produced nothing", rules)

    def test_files_are_required_to_land_in_the_workspace(self) -> None:
        rules = self.rules()
        self.assertIn("inside this workspace directory", rules)
        self.assertIn("not your home directory", rules)

    def test_the_rules_still_read_as_one_list(self) -> None:
        # Cheap structural guard: every line of the block is a bullet, so an
        # edit cannot leave prose dangling where an instruction should be.
        body = [
            line for line in executor.HARD_RULES.strip().splitlines()[1:]
            if line.strip()
        ]
        self.assertTrue(body)
        for line in body:
            with self.subTest(line=line[:40]):
                self.assertTrue(line.startswith("- ") or line.startswith("  "))


class TheAgentCannotHoldTheVerifiersIdentity(unittest.TestCase):
    """A rule in a prompt is a request; this is the control.

    Run 63 of `t_e7b0028b` researched the field, built the page, and then ran
    the sandbox verifier to check its own work. verify.py archives and commits
    a run's output, so the worker's real verify - seconds later - found a clean
    tree and recorded a complete, researched page as "this run changed nothing
    in the workspace". Run 59 had done the same an hour earlier, and HARD_RULES
    had been told to forbid it in between.

    The sandbox verifier now refuses to run unless TRIAI_RUN_ID is set. That is
    only worth anything if the agent can never have one, which is what these
    assert: the identity is built for the verify subprocess, and stripped from
    the agent's environment however the worker itself was started.
    """

    def test_the_identity_is_stripped_from_the_agent(self) -> None:
        with mock.patch.dict(
            os.environ,
            {name: "leaked" for name in executor.VERIFY_IDENTITY_ENV},
            clear=False,
        ):
            env = executor.agent_env()
        for name in executor.VERIFY_IDENTITY_ENV:
            with self.subTest(name=name):
                self.assertNotIn(name, env)

    def test_the_verifier_is_given_exactly_that_identity(self) -> None:
        env = executor.verify_env({
            "TRIAI_RUN_ID": "7",
            "TRIAI_TASK_ID": "t_abc",
        })
        self.assertEqual(env["TRIAI_RUN_ID"], "7")
        self.assertEqual(env["TRIAI_TASK_ID"], "t_abc")

    def test_run_id_is_the_token_the_verifier_keys_on(self) -> None:
        # Named first in the tuple and asserted here, so a rename cannot
        # silently decouple the stripping from the check that relies on it.
        self.assertIn("TRIAI_RUN_ID", executor.VERIFY_IDENTITY_ENV)

    def test_credentials_are_still_stripped_too(self) -> None:
        # The identity strip must not have displaced the older guarantee.
        name = next(iter(executor.CREDENTIAL_ENV_NAMES))
        with mock.patch.dict(os.environ, {name: "secret"}, clear=False):
            self.assertNotIn(name, executor.agent_env())
