"""Public-release hygiene checks for the portfolio repository.

The runtime deliberately has private planning material and local deployment
state. This test inspects the Git index, not the working tree, so operators may
keep those files locally while a public push refuses to include them.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PATHS = (
    ".planning/", "docs/HANDOFF_", "docs/CONTINUITY.md", "docs/CAPABILITY_REVIEW_",
)
PRIVATE_MARKERS = (
    "C:/Users/ardit", "C:\\Users\\ardit", "DESKTOP-JHQ7HJM", "100.67.149.86", "100.118.189.88",
)


class PublicReleaseBoundaryTests(unittest.TestCase):
    def tracked_files(self) -> list[str]:
        output = subprocess.check_output(
            ["git", "ls-files"], cwd=ROOT, text=True, encoding="utf-8",
        )
        return [line for line in output.splitlines() if line]

    def test_private_operator_material_is_not_tracked(self):
        for path in self.tracked_files():
            self.assertFalse(path.startswith(PRIVATE_PATHS), path)

    def test_public_non_test_text_does_not_contain_known_operator_markers(self):
        for relative in self.tracked_files():
            if relative.startswith(("tests/", "evidence/")):
                continue
            path = ROOT / relative
            if path.suffix.lower() not in {".md", ".py", ".ps1", ".json", ".yaml", ".yml", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8")
            for marker in PRIVATE_MARKERS:
                self.assertNotIn(marker, text, f"{marker!r} in {relative}")


if __name__ == "__main__":
    unittest.main()
