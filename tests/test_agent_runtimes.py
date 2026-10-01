"""Tri-AI could only ever spawn one agent, so two subscriptions went unused.

`run_agent` hardcoded

    argv = [hermes_bin(), "-z", build_prompt(repo, prompt)]

and every routing decision in this repository sat downstream of that line:
`model_override`, the route registry, the lane panel. All of them choose a
*model for Hermes*. None of them can choose a different agent.

That is why a Claude Pro and a ChatGPT Plus subscription - GBP 20 each,
paid monthly - were unreachable. Neither sells API access, so neither can
ever be a model id. What they sell is a CLI, and both run headless:

    claude -p "PROMPT"        Claude Pro
    codex exec "PROMPT"       ChatGPT Plus

Verified on the desktop 2026-09-30: `claude` reports "-p/--print for
non-interactive output", `codex exec` reports "Run Codex
non-interactively", and `codex login status` reports "Logged in using
ChatGPT".

So the runtime is a choice beside the model, not under it. These tests
pin the argv each runtime produces, because an argv is the whole contract
with a CLI and a wrong flag fails as an opaque spawn error.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import executor  # noqa: E402


class TheRuntimesAreDeclaredTest(unittest.TestCase):
    def test_three_runtimes_exist(self):
        self.assertTrue(hasattr(executor, "AGENT_RUNTIMES"))
        self.assertEqual(set(executor.AGENT_RUNTIMES), {"hermes", "claude", "codex"})

    def test_hermes_is_the_default(self):
        """Free first. The subscriptions are escalation, not the floor."""
        self.assertEqual(executor.DEFAULT_RUNTIME, "hermes")

    def test_an_unknown_runtime_is_refused_rather_than_guessed(self):
        with self.assertRaises(executor.UnknownRuntime):
            executor.runtime_argv("gpt4all", prompt="hi", model=None, usage_path=None)


class EachRuntimeGetsItsOwnArgvTest(unittest.TestCase):
    """A CLI contract is its flags; getting one wrong is an opaque failure."""

    def test_hermes_argv_is_unchanged(self):
        argv = executor.runtime_argv("hermes", prompt="BODY", model=None, usage_path=None)
        self.assertEqual(argv[1], "-z")
        self.assertEqual(argv[2], "BODY")

    def test_hermes_still_takes_a_model_and_usage_file(self):
        argv = executor.runtime_argv(
            "hermes", prompt="BODY", model="mistral/codestral-2508",
            usage_path=Path("u.json"))
        self.assertIn("-m", argv)
        self.assertIn("mistral/codestral-2508", argv)
        self.assertIn("--usage-file", argv)

    def test_claude_uses_print_mode(self):
        argv = executor.runtime_argv("claude", prompt="BODY", model=None, usage_path=None)
        self.assertIn("-p", argv, "without -p the CLI opens an interactive "
                                  "session and the worker hangs until timeout")
        self.assertEqual(argv[-1], "BODY")

    def test_codex_uses_exec(self):
        argv = executor.runtime_argv("codex", prompt="BODY", model=None, usage_path=None)
        self.assertIn("exec", argv, "without exec the CLI opens a TUI")
        self.assertEqual(argv[-1], "BODY")

    def test_a_subscription_runtime_is_not_handed_a_hermes_model_flag(self):
        """`-m mistral/...` means nothing to Claude and would be rejected.

        The model registry addresses Hermes' router. A subscription CLI
        runs whatever model the subscription includes, and passing an
        OmniRoute identity to it is a category error that surfaces as an
        unparseable flag.
        """
        for runtime in ("claude", "codex"):
            argv = executor.runtime_argv(
                runtime, prompt="BODY", model="mistral/codestral-2508",
                usage_path=Path("u.json"))
            self.assertNotIn("mistral/codestral-2508", argv,
                             f"{runtime} was handed a Hermes model id")
            self.assertNotIn("--usage-file", argv,
                             f"{runtime} does not write Hermes usage files")


class TheRuntimeBinaryIsResolvedTest(unittest.TestCase):
    def test_each_runtime_resolves_to_a_path_or_refuses(self):
        for runtime in executor.AGENT_RUNTIMES:
            resolved = executor.runtime_bin(runtime)
            self.assertTrue(str(resolved), f"{runtime} resolved to nothing")

    def test_an_override_is_honoured_for_testing(self):
        import os
        os.environ["TRIAI_CLAUDE_BIN"] = "C:/fake/claude.exe"
        try:
            self.assertEqual(str(executor.runtime_bin("claude")), "C:\\fake\\claude.exe")
        finally:
            os.environ.pop("TRIAI_CLAUDE_BIN", None)


class TheCeilingAppliesToEveryRuntimeTest(unittest.TestCase):
    """WinError 206 is a property of the OS, not of Hermes.

    The prompt is an argv element whichever CLI receives it, so a runtime
    added later must not quietly reintroduce the failure that blocked
    every task tonight.
    """

    def test_every_runtime_refuses_an_oversized_prompt(self):
        huge = "x" * (executor.MAX_COMMAND_LINE + 1)
        for runtime in executor.AGENT_RUNTIMES:
            with self.assertRaises(executor.PromptTooLong, msg=f"{runtime} does not check"):
                executor.runtime_argv(runtime, prompt=huge, model=None, usage_path=None)


if __name__ == "__main__":
    unittest.main()
