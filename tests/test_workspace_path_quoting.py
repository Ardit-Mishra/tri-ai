r"""A workspace path with a space must survive the agent's shell.

The desktop account is named `Ardit II`, so its sandbox is
`C:\Users\Ardit II\tri-ai-sandbox`, and the prompt handed the agent an
unquoted preamble:

    Prefix EVERY command with: cd C:\Users\Ardit II\tri-ai-sandbox &&

Bash splits that on the space, so every command the agent ran failed. Its
own words, from run 90:

    "I hit a Windows-path problem immediately ... `cd C:\Users\Ardit II\
     tri-ai-sandbox && ...` is getting interpreted as 'too many arguments'"

It then stopped and asked which quoting style to use. In an unattended run
nobody answers, so the task produced nothing and the gate correctly
rejected it. Downstream: five blocked tasks, ten waiting on them, a build
stalled for three days.

The laptop account is `ardit`, with no space, which is exactly why this
never reproduced there. The bug needed a machine nobody was watching.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import executor  # noqa: E402
import run_queue  # noqa: E402

SPACED = r"C:\Users\Ardit II\tri-ai-sandbox"


class ShellPathTest(unittest.TestCase):
    def test_the_quotes_live_in_the_template_not_the_helper(self):
        """The preamble spells the quotes out, so a caller passing a raw
        path cannot silently drop them. The helper only makes a path safe
        to sit inside them."""
        self.assertFalse(executor.shell_path(SPACED).startswith('"'))
        self.assertIn('"{repo}"', executor.CD_PREAMBLE)

    def test_backslashes_become_forward_slashes(self):
        rendered = executor.shell_path(SPACED)
        self.assertNotIn("\\", rendered)
        self.assertEqual(rendered, "C:/Users/Ardit II/tri-ai-sandbox")

    def test_a_path_without_a_space_is_unharmed(self):
        self.assertEqual(executor.shell_path(r"C:\Users\ardit\tri-ai"),
                         "C:/Users/ardit/tri-ai")

    def test_an_embedded_quote_is_escaped_so_it_cannot_break_out(self):
        """A quote closing the string early would turn the rest of the path
        into separate arguments."""
        self.assertEqual(executor.shell_path('C:/a "b/c'), 'C:/a \\"b/c')

    def test_it_accepts_a_path_object(self):
        self.assertEqual(executor.shell_path(Path("C:/x/y")), "C:/x/y")


class ItActuallyWorksInBashTest(unittest.TestCase):
    """The claim is about a real shell, so a real shell is asked."""

    def setUp(self):
        self.bash = None
        for candidate in ("bash", r"C:\Program Files\Git\bin\bash.exe"):
            try:
                probe = subprocess.run([candidate, "-c", "echo ok"],
                                       capture_output=True, text=True, timeout=20)
            except (OSError, subprocess.SubprocessError):
                continue
            if probe.returncode == 0:
                self.bash = candidate
                break
        if self.bash is None:
            self.skipTest("no bash available to prove the quoting")

    def _run(self, command: str) -> int:
        return subprocess.run([self.bash, "-c", command],
                              capture_output=True, text=True, timeout=30).returncode

    def test_the_unquoted_form_fails_exactly_as_it_did_on_the_desktop(self):
        with tempfile.TemporaryDirectory() as parent:
            workspace = Path(parent, "Ardit II", "tri-ai-sandbox")
            workspace.mkdir(parents=True)
            naked = str(workspace).replace("\\", "/")
            self.assertNotEqual(
                self._run(f"cd {naked} && pwd"), 0,
                "the bug must reproduce here, or this test proves nothing")

    def test_the_quoted_form_succeeds(self):
        with tempfile.TemporaryDirectory() as parent:
            workspace = Path(parent, "Ardit II", "tri-ai-sandbox")
            workspace.mkdir(parents=True)
            quoted = executor.shell_path(workspace)
            self.assertEqual(self._run(f'cd "{quoted}" && pwd'), 0)

    def test_a_path_without_a_space_still_succeeds(self):
        with tempfile.TemporaryDirectory() as parent:
            workspace = Path(parent, "plain", "sandbox")
            workspace.mkdir(parents=True)
            quoted = executor.shell_path(workspace)
            self.assertEqual(self._run(f'cd "{quoted}" && pwd'), 0)


class BothPreamblesAgreeTest(unittest.TestCase):
    """executor and run_queue carry the same preamble.

    Three defects in this repo came from one value written in two places and
    drifting. If one is fixed and the other is not, half the runs keep
    failing on the machine nobody watches.
    """

    def test_both_quote_the_workspace(self):
        for module in (executor, run_queue):
            with self.subTest(module=module.__name__):
                self.assertIn('"{repo}"', module.CD_PREAMBLE,
                              f"{module.__name__} interpolates an unquoted path")

    def test_the_rendered_preamble_survives_a_space(self):
        for module in (executor, run_queue):
            with self.subTest(module=module.__name__):
                rendered = module.CD_PREAMBLE.format(
                    home="C:/Users/Ardit II", repo=executor.shell_path(SPACED))
                self.assertIn('"C:/Users/Ardit II/tri-ai-sandbox"', rendered)


if __name__ == "__main__":
    unittest.main()
