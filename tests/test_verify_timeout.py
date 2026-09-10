"""Phase 2: a timed-out verify command must actually stop, whole tree.

`subprocess.run(..., shell=True, timeout=…)` kills the immediate child. Under a
shell on Windows that is cmd.exe, and its descendants survive — so a verify
command that spawns a writer can "time out", keep writing, and race the revert
and the next claim. That corruption looks like nothing went wrong, which is the
worst shape a bug can take in a system whose whole claim is that results are
verified.

Also proves the outcome is discriminated rather than encoded in an exit code:
`run_queue.py` mapped a timeout to 124, but 124 is a value a real verify command
may legitimately return, making the two indistinguishable.
"""

from __future__ import annotations

import shutil
import os
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import executor  # noqa: E402

PY = sys.executable


class VerifyOutcomesAreDiscriminated(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="triai-timeout-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_exit_zero_is_passed(self):
        r = executor.run_verify(f'"{PY}" -c "raise SystemExit(0)"', cwd=self.tmp, timeout=60)
        self.assertEqual(r.outcome, "passed")
        self.assertEqual(r.exit_code, 0)
        self.assertTrue(r.accepted)

    def test_non_zero_is_failed(self):
        r = executor.run_verify(f'"{PY}" -c "raise SystemExit(3)"', cwd=self.tmp, timeout=60)
        self.assertEqual(r.outcome, "failed")
        self.assertEqual(r.exit_code, 3)
        self.assertFalse(r.accepted)

    def test_exit_124_is_a_failure_not_a_timeout(self):
        # The exact collision the discriminated result exists to prevent.
        r = executor.run_verify(f'"{PY}" -c "raise SystemExit(124)"', cwd=self.tmp, timeout=60)
        self.assertEqual(r.outcome, "failed")
        self.assertEqual(r.exit_code, 124)

    def test_timeout_has_no_exit_code(self):
        r = executor.run_verify(f'"{PY}" -c "import time; time.sleep(30)"', cwd=self.tmp, timeout=3)
        self.assertEqual(r.outcome, "timeout")
        self.assertIsNone(r.exit_code, "a timeout must not borrow an exit code")
        self.assertFalse(r.accepted)

    def test_a_command_that_cannot_start_is_a_spawn_error_or_failure(self):
        # Through a shell this usually surfaces as a non-zero exit rather than
        # an OSError; either is a refusal, neither is a pass.
        r = executor.run_verify("definitely-not-a-real-command-xyzzy", cwd=self.tmp, timeout=60)
        self.assertIn(r.outcome, ("failed", "spawn_error"))
        self.assertFalse(r.accepted)


class TimeoutKillsTheWholeTree(unittest.TestCase):
    """The load-bearing one: a grandchild must not outlive the timeout."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="triai-tree-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_delayed_writer_spawned_by_the_verifier_never_writes(self):
        marker = self.tmp / "written-after-timeout.txt"
        writer = self.tmp / "writer.py"
        parent = self.tmp / "parent.py"

        # Grandchild: waits well past the timeout, then writes.
        writer.write_text(textwrap.dedent(f"""
            import time
            time.sleep(10)
            open(r"{marker}", "w").write("survived the timeout")
        """).strip(), encoding="utf-8")

        # Child: spawns the grandchild, then blocks so the verify command times
        # out rather than exiting on its own.
        parent.write_text(textwrap.dedent(f"""
            import subprocess, sys, time
            subprocess.Popen([sys.executable, r"{writer}"])
            time.sleep(60)
        """).strip(), encoding="utf-8")

        result = executor.run_verify(f'"{PY}" "{parent}"', cwd=self.tmp, timeout=3)

        self.assertEqual(result.outcome, "timeout")
        self.assertFalse(
            result.tree_survived,
            "the process tree survived the kill; the worker must quarantine here",
        )

        # Wait past the moment the grandchild would have written.
        deadline = time.time() + 14
        while time.time() < deadline:
            if marker.exists():
                break
            time.sleep(0.25)

        self.assertFalse(
            marker.exists(),
            "a process spawned by the verify command outlived the timeout and "
            "wrote to the workspace — it would have raced the revert",
        )

    def test_tree_survived_is_false_on_an_ordinary_timeout(self):
        r = executor.run_verify(f'"{PY}" -c "import time; time.sleep(30)"', cwd=self.tmp, timeout=3)
        self.assertEqual(r.outcome, "timeout")
        self.assertFalse(r.tree_survived)

    def test_a_writer_orphaned_by_an_exiting_parent_never_writes(self):
        """The case that defeated the first implementation.

        When the spawning parent exits, the writer is orphaned: it is on no
        parent-child map, so `taskkill /T` cannot reach it, and checking that
        the root pid died reports success while it is still alive. Reproduced by
        review on a slower machine; a job object has no map to fall off.
        """
        marker = self.tmp / "orphan-wrote.txt"
        writer = self.tmp / "orphan_writer.py"
        spawner = self.tmp / "spawner.py"

        writer.write_text(textwrap.dedent(f"""
            import time
            time.sleep(10)
            open(r"{marker}", "w").write("orphan survived")
        """).strip(), encoding="utf-8")

        # Spawns the writer and exits IMMEDIATELY, orphaning it. The shell then
        # has nothing left to wait on.
        spawner.write_text(textwrap.dedent(f"""
            import subprocess, sys
            subprocess.Popen([sys.executable, r"{writer}"])
        """).strip(), encoding="utf-8")

        # The shell sleeps so the call times out; the orphan is already detached.
        result = executor.run_verify(
            f'"{PY}" "{spawner}" && "{PY}" -c "import time; time.sleep(60)"',
            cwd=self.tmp, timeout=4,
        )

        self.assertEqual(result.outcome, "timeout")
        self.assertFalse(
            result.tree_survived,
            f"orphaned descendants survived: {result.survivors}",
        )

        deadline = time.time() + 14
        while time.time() < deadline and not marker.exists():
            time.sleep(0.25)
        self.assertFalse(
            marker.exists(),
            "an orphaned process outlived the timeout and wrote to the "
            "workspace — it would have raced the revert",
        )

    def test_a_passing_verifier_cannot_leave_a_delayed_writer(self):
        """Containment starts before the verifier can spawn anything.

        The first Job Object implementation assigned the process *after*
        ``CreateProcess`` returned.  A short-lived verifier could spawn a
        writer in that window, exit zero, and make the worker accept while the
        writer remained outside the job.  This must fail against that design.
        """
        marker = self.tmp / "passing-verifier-wrote-late.txt"
        writer = self.tmp / "passing_writer.py"
        parent = self.tmp / "passing_parent.py"

        writer.write_text(textwrap.dedent(f"""
            import time
            time.sleep(5)
            open(r"{marker}", "w").write("escaped a passing verifier")
        """).strip(), encoding="utf-8")
        parent.write_text(textwrap.dedent(f"""
            import subprocess, sys
            subprocess.Popen(
                [sys.executable, r"{writer}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        """).strip(), encoding="utf-8")

        result = executor.run_verify(f'"{PY}" "{parent}"', cwd=self.tmp, timeout=30)

        self.assertEqual(result.outcome, "passed")
        self.assertFalse(result.tree_survived)
        self.assertFalse(marker.exists(), "the writer ran before the verifier returned")
        time.sleep(7)
        self.assertFalse(
            marker.exists(),
            "a passing verifier escaped containment and wrote after acceptance",
        )

    @unittest.skipUnless(executor.IS_WINDOWS, "fake Hermes launcher is Windows-specific")
    def test_a_passing_agent_cannot_leave_a_delayed_writer(self):
        marker = self.tmp / "passing-agent-wrote-late.txt"
        writer = self.tmp / "agent_writer.py"
        agent = self.tmp / "fake_agent.py"
        launcher = self.tmp / "fake-hermes.cmd"

        writer.write_text(textwrap.dedent(f"""
            import time
            time.sleep(5)
            open(r"{marker}", "w").write("escaped a passing agent")
        """).strip(), encoding="utf-8")
        agent.write_text(textwrap.dedent(f"""
            import subprocess, sys
            subprocess.Popen(
                [sys.executable, r"{writer}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        """).strip(), encoding="utf-8")
        launcher.write_text(
            f'@echo off\r\n"{PY}" "{agent}"\r\nexit /b %ERRORLEVEL%\r\n',
            encoding="utf-8",
        )

        original = os.environ.get("TRIAI_HERMES_BIN")
        os.environ["TRIAI_HERMES_BIN"] = str(launcher)
        try:
            result = executor.run_agent(self.tmp, "do the task", timeout=30)
        finally:
            if original is None:
                os.environ.pop("TRIAI_HERMES_BIN", None)
            else:
                os.environ["TRIAI_HERMES_BIN"] = original

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertFalse(result.tree_survived, result.output)
        self.assertFalse(marker.exists(), "the writer ran before the agent returned")
        time.sleep(7)
        self.assertFalse(
            marker.exists(),
            "a passing agent escaped containment and wrote after verification could start",
        )


if __name__ == "__main__":
    unittest.main()
