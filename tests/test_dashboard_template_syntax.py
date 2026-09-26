"""The dashboard template must actually parse as JavaScript.

Every other dashboard test asserts on substrings of the embedded template, so
all of them pass on a script with a syntax error in it - and the page then
fails silently in the browser: no data, no metrics, just a badge stuck on
"connecting". That happened during the Part 2 edit, where an inserted branch
produced two `else` clauses in one `if`. Substring assertions cannot catch
that class of defect; a parser can.

Node is the parser when it is installed. It is not a project dependency, so
its absence skips rather than fails - but the skip is explicit, never a quiet
pass, so nobody reads a green run as proof the template parsed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dashboard import kaya_web as web  # noqa: E402
from test_dashboard_web import fixture_snapshot  # noqa: E402


def _inline_scripts(html: str) -> list[str]:
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL)


class TemplateSyntaxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.node = shutil.which("node")

    def test_the_template_contains_exactly_one_inline_script(self):
        self.assertEqual(len(_inline_scripts(web.HTML)), 1)

    def test_the_inline_script_parses_as_javascript(self):
        if not self.node:
            self.skipTest("node is not installed; template syntax was not parsed")
        script = _inline_scripts(web.HTML)[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "template.js"
            path.write_text(script, encoding="utf-8")
            # --check parses without executing: no network, no DOM, no side effects.
            result = subprocess.run(
                [self.node, "--check", str(path)],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(
            result.returncode, 0,
            f"embedded dashboard script does not parse:\n{result.stderr}",
        )

    def test_the_template_is_valid_html_with_the_elements_the_script_drives(self):
        # Each id the script writes into must exist in the markup, or the page
        # throws on the first snapshot instead of rendering.
        script = _inline_scripts(web.HTML)[0]
        referenced = set(re.findall(r"byId\('([A-Za-z0-9_-]+)'\)", script))
        declared = set(re.findall(r'id="([A-Za-z0-9_-]+)"', web.HTML))
        self.assertEqual(
            referenced - declared, set(),
            "the script reads element ids that the markup does not declare",
        )

    def test_the_served_page_is_the_module_template(self):
        self.assertTrue(web.HTML.startswith("<!doctype html>"))
        self.assertIn("</html>", web.HTML)

    def test_the_snapshot_payload_is_json_serialisable(self):
        json.dumps(web.snapshot_payload(fixture_snapshot()))


if __name__ == "__main__":
    unittest.main()
