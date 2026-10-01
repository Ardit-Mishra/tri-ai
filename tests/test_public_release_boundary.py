"""Public-release hygiene checks for the portfolio repository.

The runtime deliberately has private planning material and local deployment
state. This test inspects the Git index, not the working tree, so operators may
keep those files locally while a public push refuses to include them.
"""

from __future__ import annotations

import getpass
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PATHS = (
    ".planning/", "docs/HANDOFF_", "docs/CONTINUITY.md", "docs/CAPABILITY_REVIEW_",
)

# This check used to hold a list of the operator's real machine name, home
# directory and Tailnet addresses, so that it could search for them - which
# published every one of them in the repository the check exists to protect.
# It also skipped `tests/`, because that literal list had to live somewhere,
# and so it never looked at the file that was doing the leaking.
#
# Both halves are gone. Identity is matched by *shape*, so nothing private is
# written down, and nothing is skipped.
SCANNED_SUFFIXES = {".md", ".py", ".ps1", ".json", ".yaml", ".yml", ".txt"}

OPERATOR_SHAPES = (
    # The Windows default hostname. Any machine's, not one particular one.
    ("a machine name", re.compile(r"DESKTOP-[A-Z0-9]{7}")),
    # The shared address space of RFC 6598, which is the range Tailscale
    # assigns from. Written as a rule rather than spelled out, because a
    # comment naming it is still the check matching its own source.
    ("a Tailnet address",
     re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b")),
)

# No allowlist. An earlier draft kept one holding a "harmless" example
# address, and that literal tripped this very check - which is the argument
# against it: an absolute rule cannot be eroded, and fixtures that need to
# name a host can say `kaya.example` instead.


def _home_directory_shape() -> "re.Pattern[str]":
    """Match this account's home directory, without naming it.

    Derived from whoever is running the check rather than stored, so the
    operator's username never enters the repository. On someone else's
    machine it derives theirs, which is the same promise kept for them.

    Only path-shaped occurrences count. A name in a README is authorship;
    the same name after `C:\\Users\\` is a filesystem layout nobody asked for.
    """
    account = re.escape(getpass.getuser())
    return re.compile(
        r"(?:[A-Za-z]:[\\/]+Users|/home|/Users)[\\/]+" + account + r"\b",
        re.IGNORECASE,
    )


def _release_scope() -> str:
    """Who this branch's content is for, as declared in RELEASE_SCOPE.

    These checks exist to keep the *public* repository clean. Applying them
    to a private branch would forbid the operator's own planning notes and
    handoffs from ever being committed anywhere - which is how 178 KB of
    real working state came to live on a single laptop with no remote.

    Anything unrecognised reads as private, and a private branch cannot
    reach a public remote: `scripts/release_guard.py` refuses that push.
    """
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "scripts"))
    import release_guard
    return release_guard.read_scope(ROOT)


class PublicReleaseBoundaryTests(unittest.TestCase):
    def setUp(self):
        if _release_scope() != "public":
            self.skipTest(
                "RELEASE_SCOPE is not public; the public-release checks apply "
                "to the published branch. scripts/release_guard.py refuses a "
                "private branch on a public remote."
            )

    def tracked_files(self) -> list[str]:
        output = subprocess.check_output(
            ["git", "ls-files"], cwd=ROOT, text=True, encoding="utf-8",
        )
        return [line for line in output.splitlines() if line]

    def test_private_operator_material_is_not_tracked(self):
        for path in self.tracked_files():
            self.assertFalse(path.startswith(PRIVATE_PATHS), path)

    def test_no_tracked_text_carries_the_operator_identity(self):
        home = _home_directory_shape()
        findings: list[str] = []
        for relative in self.tracked_files():
            path = ROOT / relative
            if path.suffix.lower() not in SCANNED_SUFFIXES or not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for label, shape in OPERATOR_SHAPES:
                if shape.search(text):
                    findings.append(f"{relative}: {label}")
            if home.search(text):
                findings.append(f"{relative}: this account's home directory")
        # The report names the file and the kind of thing found, never the
        # value. A failure message that printed it would leak into whatever
        # log caught the failure.
        self.assertEqual(sorted(set(findings)), [])

    def test_render_blueprint_can_only_start_the_sealed_demo(self):
        manifest = (ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("--demo", manifest)
        self.assertIn("healthCheckPath: /api/snapshot", manifest)
        self.assertIn("autoDeploy: false", manifest)


if __name__ == "__main__":
    unittest.main()
