"""The dashboard launcher must not bind the Tailnet without a session key.

Found live on the desktop on 2026-09-29: `run_dashboard.ps1` bound
`kaya.example:8081` and `/api/snapshot` answered HTTP 200 with no
session. The whole board - task titles, workspace paths, log tails - was
readable by any device on the Tailnet. It is the same hole closed on the
laptop the day before, still open here.

`kaya_web` already knew how to refuse; it simply had no key.
`KAYA_SESSION_TOKEN` was expected from the environment, and **a scheduled
task does not inherit the environment of whoever starts it**, so
exporting it before `Start-ScheduledTask` changed nothing. The launcher
has to read the key itself.

Two rules, and the second is the one that matters:

* if the key file exists, pass it to the server;
* if it does not and a Tailnet bind was asked for, **refuse to start**.

Serving nothing is recoverable. Serving the board to the Tailnet
unauthenticated is not.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_dashboard.ps1"


def _powershell() -> str | None:
    for candidate in ("powershell", "pwsh",
                      r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"):
        if shutil.which(candidate):
            return candidate
    return None


class LauncherReadsTheKeyTest(unittest.TestCase):
    def test_the_script_reads_the_key_file_rather_than_the_environment(self):
        body = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("kaya-session-key.txt", body,
                      "the launcher must read the key; a scheduled task does "
                      "not inherit one from the environment")
        self.assertIn("KAYA_SESSION_TOKEN", body)

    def test_it_refuses_a_tailnet_bind_with_no_key(self):
        body = SCRIPT.read_text(encoding="utf-8")
        guard = body[body.index("kaya-session-key.txt"):]
        self.assertIn("throw", guard.split("Set-Location")[0],
                      "a Tailnet bind without a key must refuse to start")


class ItActuallyRefusesTest(unittest.TestCase):
    """Reading the source is not the same as the script behaving."""

    def setUp(self):
        self.shell = _powershell()
        if self.shell is None:
            self.skipTest("no PowerShell available")

    def _run(self, *args, profile: str):
        environment = dict(os.environ, USERPROFILE=profile)
        return subprocess.run(
            [self.shell, "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(SCRIPT), *args],
            capture_output=True, text=True, timeout=90, env=environment,
            encoding="utf-8", errors="replace")

    def test_a_tailnet_bind_without_a_key_fails_loudly(self):
        with tempfile.TemporaryDirectory() as empty:
            result = self._run("-AllowTailnetBinding", "-TailscaleAddress",
                               "203.0.113.7", "-WhatIf", profile=empty)
        self.assertNotEqual(result.returncode, 0,
                            "it started a Tailnet bind with no session key")
        self.assertIn("session key", (result.stderr + result.stdout).lower())

    def test_loopback_only_still_starts_without_a_key(self):
        """A keyless dashboard on loopback is the operator's own machine and
        must keep working; only the Tailnet bind is refused."""
        with tempfile.TemporaryDirectory() as empty:
            result = self._run("-WhatIf", profile=empty)
        self.assertEqual(result.returncode, 0, result.stderr[:300])
        self.assertIn("127.0.0.1", result.stdout)

    def test_a_present_key_is_not_echoed(self):
        with tempfile.TemporaryDirectory() as profile:
            key_dir = Path(profile, ".tri-ai")
            key_dir.mkdir(parents=True)
            (key_dir / "kaya-session-key.txt").write_text(
                "abcd-efgh-jkmn\n", encoding="utf-8")
            result = self._run("-WhatIf", profile=profile)
        self.assertNotIn("abcd-efgh-jkmn", result.stdout + result.stderr,
                         "the key must never reach a log or console")


if __name__ == "__main__":
    unittest.main()
