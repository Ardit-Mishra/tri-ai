"""Tests for the opt-in, metadata-only source indexing command."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dashboard import private_index_agent  # noqa: E402
from memory import brain  # noqa: E402


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

    def test_agent_passes_protected_directory_exclusions_to_the_indexer(self):
        output = self.root / "out" / "desktop.json"
        source = self.root / "profile"
        source.mkdir()
        payload = {
            "status": "unavailable", "synthetic": False, "item_count": 0,
            "sources": [{"id": "desktop", "label": "Desktop", "node_count": 0, "authorized": True}],
            "items": [], "diagnostic": "fixture",
        }
        with mock.patch.object(private_index_agent.private_index, "scan_authorized_roots", return_value=payload) as scan, \
             mock.patch.object(private_index_agent.private_index, "write_private_index_atomic"), \
             mock.patch.object(private_index_agent.private_index, "write_private_index_summary_atomic"):
            private_index_agent.main([
                "--output", str(output), "--root", f"Desktop={source}",
                "--exclude-directory", "AppData", "--exclude-directory", ".ssh",
            ])

        self.assertEqual(scan.call_args.kwargs["excluded_directory_names"], {"AppData", ".ssh"})

    def test_agent_can_record_a_path_free_scan_summary_in_the_local_brain(self):
        source = self.root / "very-private-source-name"
        source.mkdir()
        (source / "private-note.txt").write_text("do not retain this body", encoding="utf-8")
        output = self.root / "out" / "index.json"
        brain_path = self.root / "brain" / "brain.db"

        private_index_agent.main([
            "--output", str(output), "--root", f"Desktop={source}", "--brain", str(brain_path),
        ])

        memory = brain.connect_read_only(brain_path)
        try:
            item = memory.execute("SELECT title, body, metadata_json FROM brain_items").fetchone()
            self.assertEqual(item["title"], "Cortex index update")
            self.assertIn("1 authorized source", item["body"])
            self.assertIn("2 indexed nodes", item["body"])
            self.assertNotIn(str(source), item["body"])
            self.assertNotIn("very-private-source-name", item["body"])
            self.assertNotIn("private-note.txt", item["body"])
            self.assertNotIn("very-private-source-name", item["metadata_json"])
        finally:
            memory.close()

    def test_watch_mode_reindexes_after_each_requested_interval(self):
        output = self.root / "private" / "index.json"
        source = self.root / "source"
        source.mkdir()
        write_calls: list[Path] = []

        def write_once(path, payload):
            write_calls.append(Path(path))
            if len(write_calls) == 2:
                raise KeyboardInterrupt

        with mock.patch.object(private_index_agent.private_index, "write_private_index_atomic", side_effect=write_once), \
             mock.patch.object(private_index_agent.private_index, "write_private_index_summary_atomic"), \
             mock.patch.object(private_index_agent.time, "sleep") as sleep:
            with self.assertRaises(KeyboardInterrupt):
                private_index_agent.main([
                    "--output", str(output), "--root", f"Desktop={source}", "--watch-seconds", "15",
                ])

        self.assertEqual(write_calls, [output, output])
        sleep.assert_called_once_with(15)


if __name__ == "__main__":
    unittest.main()
