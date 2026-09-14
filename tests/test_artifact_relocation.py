"""A verify command that moves what it verified must not strand the record.

Artifacts are captured the moment the agent exits — the only point at which
"the agent produced this" is provable, and the reason criterion 3 exists. A
verify command is then free to do as it likes, and the sandbox verifier files
each run into `runs/YYYY-MM-DD-slug/`.

The moment that landed, every artifact row in the sandbox pointed at a path
that no longer existed — eight of eight. Artifact serving reaches for those
paths, and so does Telegram document delivery, which is the operator's main
channel. The fix for "I don't have time to sift through the files" had quietly
broken the thing that stops them having to.

What is reconciled here is **location only**. The row still says this run
produced this file, which is what was observed and stays true.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from support import BoardTestCase  # noqa: E402

import board  # noqa: E402
import completion_report  # noqa: E402
import worker  # noqa: E402


class RelocatingMovedArtifacts(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.tmp / "workspace"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.task_id = board.create_task(
            self.conn, title="build a page", prompt="build it",
            verify_command="python verify.py", repo=self.repo, verify_timeout=60,
        )
        self.run_id = 1

    def produce(self, name: str, body: str) -> Path:
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        board.record_run_artifacts(
            self.conn, task_id=self.task_id, run_id=self.run_id,
            artifacts=[{"path": name, "change": "??",
                        "size_bytes": path.stat().st_size}],
        )
        return path

    def recorded(self) -> list[str]:
        return [r["path"] for r in board.run_artifacts(self.conn, task_id=self.task_id)]

    def file_into_run(self, path: Path, folder: str = "runs/2026-09-14-a-page") -> Path:
        destination = self.repo / folder / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(destination))
        return destination

    def test_a_moved_artifact_is_re_pointed(self):
        page = self.produce("page.html", "<h1>the deliverable</h1>")
        self.file_into_run(page)
        self.assertEqual(
            worker._relocate_moved_artifacts(
                self.conn, self.task_id, self.run_id, self.repo), 1,
        )
        self.assertEqual(self.recorded(), ["runs/2026-09-14-a-page/page.html"])

    def test_the_re_pointed_path_actually_resolves(self):
        page = self.produce("page.html", "<h1>the deliverable</h1>")
        self.file_into_run(page)
        worker._relocate_moved_artifacts(self.conn, self.task_id, self.run_id, self.repo)
        for rel in self.recorded():
            self.assertTrue(
                (self.repo / rel).is_file(),
                f"the record still names nothing: {rel}",
            )

    def test_an_artifact_that_did_not_move_is_left_alone(self):
        self.produce("stayed.html", "<h1>still here</h1>")
        self.assertEqual(
            worker._relocate_moved_artifacts(
                self.conn, self.task_id, self.run_id, self.repo), 0,
        )
        self.assertEqual(self.recorded(), ["stayed.html"])

    def test_a_genuinely_deleted_artifact_is_not_invented(self):
        page = self.produce("gone.html", "<h1>deleted by the verifier</h1>")
        page.unlink()
        self.assertEqual(
            worker._relocate_moved_artifacts(
                self.conn, self.task_id, self.run_id, self.repo), 0,
        )
        self.assertEqual(
            self.recorded(), ["gone.html"],
            "a missing file was given a path it does not have",
        )

    def test_an_ambiguous_match_is_refused(self):
        # Two files with the same name and size. A wrong path is worse than a
        # missing one: it looks like evidence.
        page = self.produce("index.html", "<h1>aaaaaaaa</h1>")
        for folder in ("runs/one", "runs/two"):
            target = self.repo / folder
            target.mkdir(parents=True)
            (target / "index.html").write_text("<h1>aaaaaaaa</h1>", encoding="utf-8")
        page.unlink()
        self.assertEqual(
            worker._relocate_moved_artifacts(
                self.conn, self.task_id, self.run_id, self.repo), 0,
        )
        self.assertEqual(self.recorded(), ["index.html"])

    def test_size_disambiguates_a_shared_name(self):
        # Same name, different content: only one is this run's artifact.
        page = self.produce("index.html", "<h1>the real one, longer</h1>")
        decoy = self.repo / "runs" / "other"
        decoy.mkdir(parents=True)
        (decoy / "index.html").write_text("<h1>x</h1>", encoding="utf-8")
        self.file_into_run(page)
        self.assertEqual(
            worker._relocate_moved_artifacts(
                self.conn, self.task_id, self.run_id, self.repo), 1,
        )
        self.assertEqual(self.recorded(), ["runs/2026-09-14-a-page/index.html"])

    def test_it_never_adds_an_artifact_that_was_not_captured(self):
        self.produce("page.html", "<h1>recorded</h1>")
        (self.repo / "unrecorded.html").write_text("<h1>not captured</h1>", encoding="utf-8")
        worker._relocate_moved_artifacts(self.conn, self.task_id, self.run_id, self.repo)
        self.assertEqual(self.recorded(), ["page.html"])


class DeliveryNeverQueuesAMissingFile(BoardTestCase):
    """One stale row should not become a failed upload."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.tmp / "workspace"
        self.repo.mkdir()

    def documents(self, artifacts):
        return completion_report.deliverable_documents(artifacts, str(self.repo))

    def test_a_recorded_path_that_no_longer_exists_is_skipped(self):
        docs = self.documents([
            {"path": "moved-away.html", "change": "??", "size_bytes": 120},
        ])
        self.assertEqual(
            docs, (),
            "delivery queued an upload for a file that is not on disk",
        )

    def test_a_file_that_is_there_is_still_sent(self):
        page = self.repo / "here.html"
        page.write_text("<h1>present</h1>", encoding="utf-8")
        docs = self.documents([
            {"path": "here.html", "change": "??",
             "size_bytes": page.stat().st_size},
        ])
        self.assertEqual(len(docs), 1)
        self.assertTrue(Path(docs[0]["absolute"]).is_file())

    def test_a_relocated_file_is_sent_from_its_new_home(self):
        target = self.repo / "runs" / "2026-09-14-a-page"
        target.mkdir(parents=True)
        page = target / "page.html"
        page.write_text("<h1>filed</h1>", encoding="utf-8")
        docs = self.documents([
            {"path": "runs/2026-09-14-a-page/page.html", "change": "??",
             "size_bytes": page.stat().st_size},
        ])
        self.assertEqual(len(docs), 1)
        self.assertTrue(Path(docs[0]["absolute"]).is_file())


if __name__ == "__main__":
    unittest.main()
