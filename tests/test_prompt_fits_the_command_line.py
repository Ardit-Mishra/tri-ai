"""The prompt travels as an argv element, and argv has a hard ceiling.

`run_agent` builds `[hermes, "-z", prompt]`. On Windows the whole command
line is capped at 32,767 characters, and exceeding it does not truncate -
`CreateProcess` refuses with

    FileNotFoundError: [WinError 206] The filename or extension is too long

which the worker reports as an *environment* failure, so it does not count
against the task and `consecutive_failures` stays 0. The task cycles
claimed -> spawned -> environment_backoff four times in twenty-four
seconds and lands in `blocked` with no recorded reason on the row. That is
exactly what `t_4b46ec92` did, and it looked like a board bug for hours.

The cause was a change made in this repository tonight: inlining skill
text took the designer's prompt from roughly 3 KB to 41,789 characters.
The skills were the right fix; sending them down a channel with a ceiling
nobody had measured was not.

So the ceiling is measured here, once, in the one place that builds the
argv - not left to whichever role happens to assemble the longest brief.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import capabilities  # noqa: E402
import executor  # noqa: E402
import taste  # noqa: E402


class TheCeilingIsDeclaredTest(unittest.TestCase):
    def test_there_is_a_documented_argv_ceiling(self):
        self.assertTrue(
            hasattr(executor, "MAX_COMMAND_LINE"),
            "nothing in the executor knows the command line has a limit, so "
            "a long prompt fails as WinError 206 at spawn time")
        # Windows' documented cap is 32,767 for the whole command line; the
        # constant must leave room for the executable path and the flags.
        self.assertLessEqual(executor.MAX_COMMAND_LINE, 32_000)
        self.assertGreaterEqual(executor.MAX_COMMAND_LINE, 8_000)


class AnOversizedPromptIsRefusedNotSpawnedTest(unittest.TestCase):
    """Fail closed, with a message that names the cause.

    WinError 206 says nothing about prompts. A refusal raised before the
    spawn can say exactly what is too long and by how much, which is the
    difference between a four-hour diagnosis and a one-line fix.
    """

    def test_a_prompt_over_the_ceiling_is_refused_before_spawning(self):
        with self.assertRaises(executor.PromptTooLong) as caught:
            executor.check_prompt_fits("x" * (executor.MAX_COMMAND_LINE + 1))
        message = str(caught.exception)
        self.assertIn("prompt", message.lower())
        self.assertRegex(message, r"\d", "the refusal does not say how long it is")

    def test_a_prompt_within_the_ceiling_passes(self):
        executor.check_prompt_fits("x" * 1000)

    def test_run_agent_checks_before_it_spawns(self):
        """A guard nothing calls is the defect this repo keeps finding."""
        import ast
        import inspect
        source = inspect.getsource(executor.run_agent)
        tree = ast.parse(source.lstrip())
        called = {node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertIn(
            "check_prompt_fits", called,
            "run_agent builds the argv and never measures it")


class TheRealBriefsFitTest(unittest.TestCase):
    """The ceiling is only useful if the system's own prompts clear it.

    A guard that every real task trips is an outage, not a fix. These
    assert the two largest briefs this system assembles stay inside it.
    """

    def _worst_case(self, role: str) -> int:
        contract = capabilities.resolve_contract(role, None)
        brief = capabilities.brief_block(contract, task_prompt="build a portfolio site")
        return len(brief) + len(taste.brief_block()) + 2000  # 2k for the task itself

    def test_the_designer_brief_fits(self):
        size = self._worst_case("designer")
        self.assertLess(
            size, executor.MAX_COMMAND_LINE,
            f"the designer assembles {size:,} characters against a ceiling of "
            f"{executor.MAX_COMMAND_LINE:,}; lower DEFAULT_SKILL_BUDGET")

    def test_the_researcher_brief_fits(self):
        size = self._worst_case("researcher")
        self.assertLess(size, executor.MAX_COMMAND_LINE, f"{size:,} characters")

    def test_the_explicit_contract_that_broke_it_fits(self):
        """t_4b46ec92's own contract, which produced 41,789 characters."""
        contract = capabilities.resolve_contract(
            "builder", ["web_research", "taste", "motion_design",
                        "frontend_engineering", "browser_qa"])
        brief = capabilities.brief_block(
            contract, task_prompt="portfolio website, many animations, not AI slop")
        size = len(brief) + len(taste.brief_block()) + 2000
        self.assertLess(
            size, executor.MAX_COMMAND_LINE,
            f"the contract that caused WinError 206 still assembles {size:,}")


if __name__ == "__main__":
    unittest.main()
