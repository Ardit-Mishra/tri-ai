"""Tests for the private, metadata-only file index."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dashboard import private_index  # noqa: E402


class PrivateIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_scan_builds_one_metadata_node_per_folder_and_file(self):
        documents = self.root / "operator-documents"
        nested = documents / "projects"
        nested.mkdir(parents=True)
        report = nested / "report.txt"
        report.write_text("private contents must not enter the index", encoding="utf-8")

        graph = private_index.scan_authorized_roots({"Documents": documents})

        self.assertEqual(graph["status"], "indexed")
        self.assertFalse(graph["synthetic"])
        self.assertEqual(graph["item_count"], 3)
        self.assertEqual(graph["sources"], [{
            "id": "documents", "label": "Documents", "node_count": 3, "authorized": True,
        }])
        items = {item["relative_path"]: item for item in graph["items"]}
        self.assertEqual(set(items), {".", "projects", "projects/report.txt"})
        self.assertEqual(items["."]["kind"], "folder")
        self.assertIsNone(items["."]["parent_id"])
        self.assertEqual(items["projects"]["parent_id"], items["."]["id"])
        self.assertEqual(items["projects/report.txt"]["parent_id"], items["projects"]["id"])
        self.assertEqual(items["projects/report.txt"]["name"], "report.txt")
        self.assertEqual(items["projects/report.txt"]["label"], "report.txt")
        self.assertEqual(items["projects/report.txt"]["size"], 41)
        self.assertIsInstance(items["projects/report.txt"]["modified_at"], str)
        self.assertEqual(items["projects/report.txt"]["provenance"], "documents:projects/report.txt")
        json.dumps(graph)

    def test_scan_never_opens_file_contents(self):
        root = self.root / "authorized"
        root.mkdir()
        (root / "secret.txt").write_text("classified", encoding="utf-8")

        with mock.patch("builtins.open", side_effect=AssertionError("content read")):
            graph = private_index.scan_authorized_roots({"Vault": root})

        self.assertEqual(graph["item_count"], 2)

    def test_scan_is_deterministic_and_does_not_expose_absolute_paths_in_labels(self):
        root = self.root / "sensitive-location"
        root.mkdir()
        (root / "b.txt").write_text("b", encoding="utf-8")
        (root / "a.txt").write_text("a", encoding="utf-8")

        first = private_index.scan_authorized_roots({"Work Files": root})
        second = private_index.scan_authorized_roots({"Work Files": root})

        self.assertEqual(
            [(item["id"], item["relative_path"]) for item in first["items"]],
            [(item["id"], item["relative_path"]) for item in second["items"]],
        )
        self.assertEqual([item["relative_path"] for item in first["items"]], [".", "a.txt", "b.txt"])
        labels = [source["label"] for source in first["sources"]]
        labels.extend(item["label"] for item in first["items"])
        self.assertNotIn(str(root), json.dumps(labels))
        self.assertTrue(all(not Path(label).is_absolute() for label in labels))

    def test_scan_skips_symlinks_instead_of_following_them(self):
        root = self.root / "authorized"
        outside = self.root / "outside"
        root.mkdir()
        outside.mkdir()
        (outside / "outside.txt").write_text("do not index", encoding="utf-8")
        link = root / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (NotImplementedError, OSError):
            self.skipTest("directory symlinks are unavailable on this host")

        graph = private_index.scan_authorized_roots({"Authorized": root})

        self.assertEqual([item["relative_path"] for item in graph["items"]], ["."])
        self.assertNotIn("outside.txt", json.dumps(graph))

    def test_scan_skips_explicitly_excluded_directory_names(self):
        root = self.root / "profile"
        protected = root / "AppData"
        project = root / "projects"
        protected.mkdir(parents=True)
        project.mkdir()
        (protected / "credentials.txt").write_text("do not index", encoding="utf-8")
        (project / "readme.md").write_text("safe metadata", encoding="utf-8")

        graph = private_index.scan_authorized_roots(
            {"Desktop Profile": root}, excluded_directory_names={"AppData"},
        )

        paths = {item["relative_path"] for item in graph["items"]}
        self.assertEqual(paths, {".", "projects", "projects/readme.md"})
        self.assertNotIn("credentials.txt", json.dumps(graph))

    def test_nonexistent_and_unreadable_roots_are_reported_without_path_leaks(self):
        missing = self.root / "private" / "missing"
        denied = self.root / "private" / "denied"
        denied.mkdir(parents=True)
        real_scandir = os.scandir

        def refuse_denied(path):
            if Path(path) == denied:
                raise PermissionError("access denied to an absolute private path")
            return real_scandir(path)

        with mock.patch.object(private_index.os, "scandir", side_effect=refuse_denied):
            graph = private_index.scan_authorized_roots({"Missing": missing, "Denied": denied})

        self.assertEqual(graph["status"], "unavailable")
        self.assertEqual(graph["item_count"], 0)
        self.assertEqual([source["node_count"] for source in graph["sources"]], [0, 0])
        self.assertIn("Missing: root does not exist", graph["diagnostic"])
        self.assertIn("Denied: root is unreadable", graph["diagnostic"])
        self.assertNotIn(str(self.root), json.dumps(graph))

    def test_loader_validates_contract_and_rejects_absolute_relative_paths(self):
        path = self.root / "index.json"
        payload = private_index.scan_authorized_roots({"Vault": self.root})
        path.write_text(json.dumps(payload), encoding="utf-8")

        loaded = private_index.load_private_index(path)

        self.assertEqual(loaded, payload)
        loaded["items"][0]["relative_path"] = str(self.root.resolve())
        path.write_text(json.dumps(loaded), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "relative_path"):
            private_index.load_private_index(path)

    def test_validation_rejects_mismatched_counts_and_unknown_parents(self):
        payload = private_index.scan_authorized_roots({"Vault": self.root})
        payload["item_count"] = 99
        with self.assertRaisesRegex(ValueError, "item_count"):
            private_index.validate_private_index(payload)

        payload = private_index.scan_authorized_roots({"Vault": self.root})
        payload["items"][0]["parent_id"] = "private:missing"
        with self.assertRaisesRegex(ValueError, "parent_id"):
            private_index.validate_private_index(payload)

    def test_atomic_writer_replaces_the_destination_and_leaves_no_temp_file(self):
        destination = self.root / "state" / "private-index.json"
        destination.parent.mkdir()
        destination.write_text('{"old": true}', encoding="utf-8")
        payload = private_index.scan_authorized_roots({"Vault": self.root})

        private_index.write_private_index_atomic(destination, payload)

        self.assertEqual(private_index.load_private_index(destination), payload)
        self.assertEqual([path.name for path in destination.parent.iterdir()], [destination.name])

    def test_summary_sidecar_has_only_counts_and_can_be_matched_to_its_index(self):
        source = self.root / "authorized"
        source.mkdir()
        (source / "private-name.txt").write_text("content must stay local", encoding="utf-8")
        destination = self.root / "state" / "desktop.json"
        payload = private_index.scan_authorized_roots({"Desktop": source})

        private_index.write_private_index_atomic(destination, payload)
        summary_path = private_index.write_private_index_summary_atomic(destination, payload)
        summary = private_index.load_private_index_summary(destination)

        self.assertEqual(summary_path.name, "desktop.summary.json")
        self.assertEqual(summary["item_count"], 2)
        self.assertEqual(summary["sources"], [{"id": "desktop", "node_count": 2}])
        self.assertEqual(summary["folder_counts"], {"desktop": 1})
        serialized = json.dumps(summary)
        self.assertNotIn("private-name.txt", serialized)
        self.assertNotIn("content must stay local", serialized)
        self.assertNotIn(str(source), serialized)


if __name__ == "__main__":
    unittest.main()
