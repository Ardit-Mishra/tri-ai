"""Phase 2, criterion 2 (VERIFY-04): a fabricated upstream result cannot propagate.

Existence and non-emptiness are not enough on their own. An artifact path that
is absolute, traverses with ``..``, or resolves through a symlink could point at
any non-empty file on the machine and satisfy the check while proving nothing
about the upstream task — a gate that cannot fail.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import BoardTestCase  # noqa: E402

import board  # noqa: E402
import executor  # noqa: E402


class ArtifactPathContainment(unittest.TestCase):
    """`resolve_artifact` refuses anything outside the declaring workspace."""

    def setUp(self):
        import tempfile, shutil
        self.tmp = Path(tempfile.mkdtemp(prefix="triai-artifact-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.workspace = self.tmp / "workspace"
        (self.workspace / "sub").mkdir(parents=True)
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.secret = self.outside / "unrelated.txt"
        self.secret.write_text("a real, non-empty, unrelated file", encoding="utf-8")

    def test_a_contained_relative_path_resolves(self):
        (self.workspace / "sub" / "report.json").write_text("{}", encoding="utf-8")
        got = executor.resolve_artifact(self.workspace, "sub/report.json")
        self.assertTrue(got.exists())
        self.assertEqual(got.name, "report.json")

    def test_an_absolute_path_is_refused(self):
        with self.assertRaises(executor.ArtifactEscape):
            executor.resolve_artifact(self.workspace, str(self.secret))

    def test_a_drive_relative_path_is_refused(self):
        # ``C:foo`` is not absolute on Windows, but it is not a portable
        # workspace-relative artifact declaration either.  It uses the current
        # directory for drive C and must never acquire meaning through joining.
        with self.assertRaises(executor.ArtifactEscape):
            executor.resolve_artifact(self.workspace, "C:drive-relative.txt")

    def test_a_traversal_is_refused(self):
        with self.assertRaises(executor.ArtifactEscape):
            executor.resolve_artifact(self.workspace, "../outside/unrelated.txt")

    def test_a_symlink_out_of_the_workspace_is_refused(self):
        link = self.workspace / "escape.txt"
        try:
            link.symlink_to(self.secret)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        # The file exists and is non-empty, so a naive check would pass it.
        self.assertTrue(link.exists() and link.stat().st_size > 0)
        with self.assertRaises(executor.ArtifactEscape):
            executor.resolve_artifact(self.workspace, "escape.txt")


class UpstreamArtifactsGateTheTask(BoardTestCase):
    """The check runs before the agent, and reports every problem it finds."""

    def make_pair(self, artifacts, *, repo=None):
        repo = repo or self.tmp / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        parent = board.create_task(
            self.conn,
            title="upstream",
            prompt="produce the artifact",
            verify_command="true",
            repo=repo,
            expected_artifacts=artifacts,
        )
        child = board.create_task(
            self.conn,
            title="downstream",
            prompt="consume the artifact",
            verify_command="true",
            repo=repo,
            parents=[parent],
        )
        return repo, parent, child

    def test_a_present_non_empty_artifact_passes(self):
        repo, _parent, child = self.make_pair(["out/result.json"])
        (repo / "out").mkdir(parents=True, exist_ok=True)
        (repo / "out" / "result.json").write_text('{"ok":true}', encoding="utf-8")
        ok, detail = executor.check_upstream_artifacts(self.conn, child)
        self.assertTrue(ok, detail)

    def test_a_missing_artifact_fails(self):
        _repo, _parent, child = self.make_pair(["out/result.json"])
        ok, detail = executor.check_upstream_artifacts(self.conn, child)
        self.assertFalse(ok)
        self.assertIn("missing artifact", detail)

    def test_an_empty_artifact_fails(self):
        repo, _parent, child = self.make_pair(["out/result.json"])
        (repo / "out").mkdir(parents=True, exist_ok=True)
        (repo / "out" / "result.json").write_text("", encoding="utf-8")
        ok, detail = executor.check_upstream_artifacts(self.conn, child)
        self.assertFalse(ok)
        self.assertIn("empty artifact", detail)

    def test_an_escaping_artifact_fails_even_though_the_target_is_real(self):
        outside = self.tmp / "elsewhere"
        outside.mkdir(parents=True, exist_ok=True)
        (outside / "real.txt").write_text("definitely not empty", encoding="utf-8")
        _repo, _parent, child = self.make_pair(["../elsewhere/real.txt"])
        ok, detail = executor.check_upstream_artifacts(self.conn, child)
        self.assertFalse(ok)
        self.assertIn("outside its workspace", detail)

    def test_the_agent_is_never_invoked_when_the_check_fails(self):
        # The point of running this before the agent: a fabricated upstream
        # result must not get a chance to propagate. Any attempt to launch the
        # agent during the check is a hard failure.
        _repo, _parent, child = self.make_pair(["out/result.json"])
        called = []
        original = executor.run_agent
        executor.run_agent = lambda *a, **k: called.append(1)
        try:
            ok, _ = executor.check_upstream_artifacts(self.conn, child)
        finally:
            executor.run_agent = original
        self.assertFalse(ok)
        self.assertEqual(called, [], "the agent was invoked despite a failed check")


if __name__ == "__main__":
    unittest.main()
