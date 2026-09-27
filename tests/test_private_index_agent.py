"""Tests for the opt-in, metadata-only source indexing command."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dashboard import private_index_agent  # noqa: E402


class PrivateIndexAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_agent_writes_a_valid_metadata_index_without_file_contents_or_absolute_paths(self):
        source = self.root / "authorized"
        source.mkdir()
        (source / "notes.txt").write_text("private content must not leave this machine", encoding="utf-8")
        output = self.root / "out" / "desktop.json"

        exit_code = private_index_agent.main([
            "--output", str(output), "--root", f"Desktop={source}",
        ])

        self.assertEqual(exit_code, 0)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["item_count"], 2)
        self.assertNotIn("private content", json.dumps(payload))
        self.assertNotIn(str(source), json.dumps(payload))

    def test_agent_refuses_malformed_root_argument_before_writing(self):
        output = self.root / "out" / "desktop.json"

        with self.assertRaisesRegex(ValueError, "LABEL=PATH"):
            private_index_agent.main(["--output", str(output), "--root", "missing-separator"])

        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
