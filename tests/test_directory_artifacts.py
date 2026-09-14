"""A run that writes into a new directory must record the files, not the directory.

Task `t_a180b31e` came in from Telegram asking for a "home page" with a live
analog clock. The agent built `clock/index.html`, `clock/script.js` and
`clock/style.css` - three valid files, exactly the deliverable.

`git status --porcelain` reported that as a single line:

    ?? clock/

Git collapses an untracked directory unless asked not to. Everything reading
that output saw one entry that names a directory and no file. The sandbox
verifier concluded the run had produced nothing and exited 1; the worker duly
reverted correct work; the revert could not remove the directory on Windows, so
the workspace was quarantined and the whole worker stopped. One missing flag,
and a successful run ended as a blocked task and a halted kernel.

`--untracked-files=all` is the fix, and it belongs on the allowlist as its own
form so nothing can quietly fall back to the collapsing one.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from support import BoardTestCase  # noqa: E402

import executor  # noqa: E402
import worker  # noqa: E402


class DirectoryArtifacts(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.tmp / "workspace"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "TriAI Test"], cwd=self.repo, check=True)
        (self.repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=self.repo, check=True)

    def build_site(self) -> None:
        """Exactly what the agent produced for t_a180b31e."""
        site = self.repo / "clock"
        site.mkdir()
        (site / "index.html").write_text("<!doctype html><h1>clock</h1>", encoding="utf-8")
        (site / "script.js").write_text("// tick\n", encoding="utf-8")
        (site / "style.css").write_text("body{margin:0}\n", encoding="utf-8")

    def test_files_in_a_new_directory_are_recorded_individually(self):
        self.build_site()
        recorded = {a["path"].replace("\\", "/") for a in worker._porcelain_artifacts(self.repo)}
        self.assertEqual(
            recorded,
            {"clock/index.html", "clock/script.js", "clock/style.css"},
            "a new directory's files were not enumerated",
        )

    def test_the_directory_itself_is_never_recorded_as_an_artifact(self):
        self.build_site()
        paths = [a["path"].replace("\\", "/") for a in worker._porcelain_artifacts(self.repo)]
        self.assertNotIn("clock/", paths)
        for p in paths:
            self.assertFalse(p.endswith("/"), f"recorded a directory, not a file: {p}")

    def test_every_recorded_artifact_has_a_real_size(self):
        # The directory entry carried size=None, which is how "nothing to
        # deliver" reached the completion card.
        self.build_site()
        for a in worker._porcelain_artifacts(self.repo):
            self.assertIsNotNone(a.get("size_bytes"), f"{a['path']} has no size")
            self.assertGreater(a["size_bytes"], 0)

    def test_nested_directories_are_enumerated_too(self):
        deep = self.repo / "site" / "assets" / "img"
        deep.mkdir(parents=True)
        (deep / "logo.svg").write_text("<svg/>", encoding="utf-8")
        recorded = {a["path"].replace("\\", "/") for a in worker._porcelain_artifacts(self.repo)}
        self.assertIn("site/assets/img/logo.svg", recorded)

    def test_top_level_and_modified_files_still_work(self):
        # The enumerating flag must not disturb what already worked.
        (self.repo / "page.html").write_text("<h1>new</h1>", encoding="utf-8")
        (self.repo / "seed.txt").write_text("changed\n", encoding="utf-8")
        recorded = {a["path"].replace("\\", "/"): a["change"]
                    for a in worker._porcelain_artifacts(self.repo)}
        self.assertIn("page.html", recorded)
        self.assertIn("seed.txt", recorded)
        self.assertEqual(recorded["seed.txt"], "M")

    def test_a_clean_tree_still_records_nothing(self):
        self.assertEqual(worker._porcelain_artifacts(self.repo), [])


class EnumeratingFormIsAllowlisted(unittest.TestCase):
    """The boundary must permit the enumerating form and nothing looser."""

    def test_the_enumerating_form_is_allowed(self):
        self.assertTrue(
            executor._git_form_allowed(
                ["status", "--porcelain", "--untracked-files=all"]
            )
        )

    def test_the_collapsing_form_is_still_allowed(self):
        # Cleanliness assertions legitimately use it; only enumeration needs -u.
        self.assertTrue(executor._git_form_allowed(["status", "--porcelain"]))

    def test_no_arbitrary_status_flag_slipped_in(self):
        for bad in (
            ["status", "--porcelain", "--ignored"],
            ["status", "--porcelain", "--untracked-files=all", "--ignored"],
            ["status"],
        ):
            with self.subTest(bad=bad):
                self.assertFalse(executor._git_form_allowed(bad))


if __name__ == "__main__":
    unittest.main()
