"""Keep what a rejected run made, where a person can still reach it.

Run 82 of `t_7220dc1d` researched its subject, wrote a brief, and built a
12.7 KB page that parsed and kept every one of its five promises. The taste
gate rejected it for one thing - no image, svg or background-image anywhere -
and the worker then reverted the workspace. The board recorded nothing,
because `_record_artifacts` is only reached on the pass branch, so the
completion card told the operator "produced no files in the workspace".

That sentence was false, and it is the reason the failure rate reads worse
than the system behaves. The page was recoverable the whole time, sitting in
`stash@{0}: triai-revert:t_7220dc1d:82` on a machine the operator cannot see
from a phone.

The rule stays: a page with no imagery is not a designed page, and the gate is
right to fail it. What changes is that failing stops meaning vanishing. The
output is copied into the run directory before the revert, so the card can
name it, a follow-up can start from it, and the operator can look at the thing
they asked for and judge it themselves.

Deliberately NOT recorded as a board artifact. `record_run_artifacts` means
"delivered, and resolvable inside the workspace", and there is already a
checker that flags recorded artifacts which no longer exist. Rejected output
is a different thing and gets a different home.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import preserve  # noqa: E402


class _Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.repo = root / "workspace"
        self.run_dir = root / "runs" / "t_x" / "82"
        (self.repo / "nested").mkdir(parents=True)
        self.run_dir.mkdir(parents=True)
        (self.repo / "index.html").write_text("<h1>page</h1>", encoding="utf-8")
        (self.repo / "BRIEF.md").write_text("# brief", encoding="utf-8")
        (self.repo / "nested" / "style.css").write_text("body{}", encoding="utf-8")

    def artifacts(self, *paths):
        return [{"path": p, "change": "??"} for p in paths]


class PreserveTest(_Case):
    def test_the_files_are_copied_into_the_run_directory(self):
        preserve.keep(self.repo, self.artifacts("index.html", "BRIEF.md"),
                      self.run_dir)
        kept = self.run_dir / preserve.FOLDER
        self.assertEqual((kept / "index.html").read_text(encoding="utf-8"),
                         "<h1>page</h1>")
        self.assertTrue((kept / "BRIEF.md").exists())

    def test_the_workspace_is_not_touched(self):
        """Copy, never move. The revert is what clears the workspace, and it
        stashes - two mechanisms removing the same file would race."""
        preserve.keep(self.repo, self.artifacts("index.html"), self.run_dir)
        self.assertTrue((self.repo / "index.html").exists())

    def test_a_nested_path_keeps_its_shape(self):
        preserve.keep(self.repo, self.artifacts("nested/style.css"), self.run_dir)
        self.assertTrue((self.run_dir / preserve.FOLDER / "nested" / "style.css").exists())

    def test_a_manifest_records_what_was_kept_and_why_it_was_rejected(self):
        preserve.keep(self.repo, self.artifacts("index.html"), self.run_dir,
                      reason="no image, svg or background-image anywhere",
                      stash="triai-revert:t_x:82")
        manifest = json.loads(
            (self.run_dir / preserve.MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(manifest["files"], ["index.html"])
        self.assertIn("background-image", manifest["reason"])
        self.assertEqual(manifest["stash"], "triai-revert:t_x:82")

    def test_nothing_produced_writes_no_manifest(self):
        """An empty folder claiming preserved output is worse than none."""
        preserve.keep(self.repo, [], self.run_dir)
        self.assertFalse((self.run_dir / preserve.MANIFEST).exists())

    def test_a_missing_file_is_skipped_rather_than_raising(self):
        """Bookkeeping must never turn a failure into a crash."""
        kept = preserve.keep(
            self.repo, self.artifacts("index.html", "gone.html"), self.run_dir)
        self.assertEqual(kept, ("index.html",))

    def test_a_path_escaping_the_workspace_is_refused(self):
        """A `..` in a porcelain path would copy from outside the workspace."""
        kept = preserve.keep(
            self.repo, self.artifacts("../secrets.txt"), self.run_dir)
        self.assertEqual(kept, ())

    def test_an_unwritable_destination_does_not_raise(self):
        kept = preserve.keep(self.repo, self.artifacts("index.html"),
                             self.repo / "index.html" / "impossible")
        self.assertEqual(kept, ())


class ReadTest(_Case):
    def test_reading_back_gives_the_manifest(self):
        preserve.keep(self.repo, self.artifacts("index.html"), self.run_dir,
                      reason="no imagery", stash="triai-revert:t_x:82")
        kept = preserve.read(self.run_dir)
        self.assertEqual(kept.files, ("index.html",))
        self.assertEqual(kept.reason, "no imagery")
        self.assertTrue(kept.paths[0].exists())

    def test_reading_a_run_that_preserved_nothing_is_empty_not_an_error(self):
        self.assertEqual(preserve.read(self.run_dir).files, ())

    def test_a_corrupt_manifest_reads_as_empty(self):
        (self.run_dir / preserve.MANIFEST).write_text("{not json",
                                                      encoding="utf-8")
        self.assertEqual(preserve.read(self.run_dir).files, ())


class PurityTest(unittest.TestCase):
    def test_the_module_runs_no_git_and_opens_no_board(self):
        """It copies files. The revert owns git; the board owns the board."""
        source = (Path(__file__).resolve().parents[1] / "src" / "preserve.py"
                  ).read_text(encoding="utf-8")
        for forbidden in ("import board", "subprocess", "sqlite3"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
