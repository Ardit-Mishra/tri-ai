"""Record whether the agent committed during its own turn.

Ten failures - roughly a fifth of all of them - say "this run changed nothing
in the workspace" after the agent made two to thirty tool calls. Five
hypotheses were tested against the retained evidence and all five were wrong:

  * the agent ran the verifier itself, which archives and commits (the two
    runs that mention `verify.py` only describe it);
  * the work landed outside the workspace because `cd` does not persist (the
    only stray file in the home directory predates every run by five months);
  * a `.gitignore` hid it (the sandbox has none, and `git check-ignore` says
    every candidate path is visible);
  * the files went into `runs/`, which the gate skips (it does not skip it);
  * the agent's report was simply false (possible, but thirty tool calls is
    not nothing, and nothing retained can distinguish it).

So the class is undiagnosable from what is kept, and the honest fix is not a
sixth theory but an instrument. The single most discriminating fact is
whether HEAD moved while the agent was running: a commit during the turn
produces exactly this signature - real work, then a clean tree - and nothing
currently records it.

This does not change any outcome. A run whose HEAD moved still fails the gate
on its own terms.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import BoardTestCase  # noqa: E402

import board  # noqa: E402
import executor  # noqa: E402
import ledger as ledger_module  # noqa: E402
import worker  # noqa: E402


def _repo(root: Path) -> Path:
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"],
                   cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "marker.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)
    return root


class HeadCommitTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self.repo = _repo(Path(tempfile.mkdtemp(prefix="triai-head-")) / "r")

    def test_it_reads_the_current_commit(self):
        expected = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo,
            capture_output=True, text=True).stdout.strip()
        self.assertEqual(executor.head_commit(self.repo), expected)

    def test_it_changes_when_a_commit_is_made(self):
        before = executor.head_commit(self.repo)
        (self.repo / "new.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "second"], cwd=self.repo, check=True)
        self.assertNotEqual(executor.head_commit(self.repo), before)

    def test_a_directory_that_is_not_a_repo_reports_none(self):
        import tempfile
        self.assertIsNone(executor.head_commit(Path(tempfile.mkdtemp())))

    def test_a_repo_with_no_commits_reports_none_rather_than_raising(self):
        import tempfile
        empty = Path(tempfile.mkdtemp(prefix="triai-empty-")) / "r"
        empty.mkdir()
        subprocess.run(["git", "init", "-q", str(empty)], check=True)
        self.assertIsNone(executor.head_commit(empty))


class HeadMovedReachesTheLedgerTest(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ledger = self.tmp / "ledger.jsonl"
        self.runs = self.tmp / "runs"
        self.repo = _repo(self.tmp / "workspace")

    def _attempt(self, *, agent_commits, passing=False):
        task_id = board.create_task(
            self.conn, title="Build a page", prompt="build a page",
            verify_command="python -c \"import sys; sys.exit(%d)\""
                           % (0 if passing else 1),
            repo=self.repo, verify_timeout=60, agent_role="frontend_builder",
        )

        def fake_agent(*args, **kwargs):
            # `run_agent` is doubled here, so it cannot also be the thing that
            # computes `head_moved` — that wiring is proved against the real
            # function in RunAgentComputesHeadMovedTest below. What this
            # fixture proves is the carrying: worker -> ledger -> verify log.
            if agent_commits:
                (self.repo / "page.html").write_text("<p>x</p>", encoding="utf-8")
                subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
                subprocess.run(["git", "commit", "-qm", "archived by the agent"],
                               cwd=self.repo, check=True)
            return executor.AgentResult(0, "done\n", 1.0, model="m",
                                        model_source="usage_file", api_calls=7,
                                        head_moved=bool(agent_commits))

        verify = executor.VerifyResult(
            "passed" if passing else "failed", 0 if passing else 1,
            "verify: this run changed nothing in the workspace", 0.1)
        claimed = self.kb.claim_task(self.conn, task_id,
                                     claimer=worker.worker_id())
        with mock.patch.object(executor, "run_agent", side_effect=fake_agent), \
             mock.patch.object(executor, "run_verify", return_value=verify):
            worker.execute_task(self.conn, claimed, ledger_path=self.ledger,
                                runs_root=self.runs)
        return ledger_module.read_entries(self.ledger)[-1]

    def test_a_commit_during_the_turn_is_recorded(self):
        self.assertIs(self._attempt(agent_commits=True)["head_moved"], True)

    def test_an_ordinary_turn_records_that_head_held_still(self):
        self.assertIs(self._attempt(agent_commits=False)["head_moved"], False)

    def test_it_is_recorded_on_a_pass_too(self):
        """Not only a failure diagnostic: a pass whose HEAD moved is worth
        seeing, because the gate then judged an already-committed tree."""
        self.assertIn("head_moved", self._attempt(agent_commits=False,
                                                  passing=True))

    def test_the_verify_log_says_so_when_the_tree_looks_untouched(self):
        """The message that sent five hypotheses down five wrong paths."""
        self._attempt(agent_commits=True)
        logs = list((self.runs).rglob("verify.log"))
        self.assertTrue(logs)
        text = "\n".join(p.read_text(encoding="utf-8") for p in logs)
        self.assertIn("committed", text.casefold())

    def test_no_note_is_added_when_head_held_still(self):
        self._attempt(agent_commits=False)
        text = "\n".join(p.read_text(encoding="utf-8")
                         for p in (self.runs).rglob("verify.log"))
        self.assertNotIn("committed", text.casefold())


class RunAgentComputesHeadMovedTest(unittest.TestCase):
    """The half the worker fixture cannot reach, against the real `run_agent`.

    Only the process launch is doubled, so the HEAD reads either side of it
    are the production ones.
    """

    def setUp(self) -> None:
        import tempfile
        self.repo = _repo(Path(tempfile.mkdtemp(prefix="triai-ra-")) / "r")

    class _Proc:
        """The child `run_agent` drains and waits on."""

        def __init__(self):
            import io as _io
            self.stdout = _io.StringIO("")
            self.returncode = 0

        def wait(self, timeout=None):
            return 0

        def communicate(self, timeout=None):
            return "", ""

    class _Containment:
        """Enough of the Containment protocol for run_agent to finish.

        The side effect fires on construction, which is the moment the real
        one launches the agent - so it lands between run_agent's two HEAD
        reads, exactly where a committing agent would.
        """

        def __init__(self, on_run, proc):
            on_run()
            self.proc = proc

        def terminate_tree(self, grace=None):
            return [], []

        def close(self):
            pass

    def _run(self, *, commits):
        def on_run():
            if commits:
                (self.repo / "made.py").write_text("x = 1\n", encoding="utf-8")
                subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
                subprocess.run(["git", "commit", "-qm", "agent committed"],
                               cwd=self.repo, check=True)

        with mock.patch.object(executor, "hermes_bin",
                               return_value=Path("hermes")), \
             mock.patch.object(
                 executor, "spawn_contained",
                 side_effect=lambda *a, **k: self._Containment(
                     on_run, self._Proc())):
            return executor.run_agent(self.repo, "build a page", timeout=30)

    def test_a_commit_during_the_run_is_detected(self):
        self.assertIs(self._run(commits=True).head_moved, True)

    def test_no_commit_reports_false_not_none(self):
        """False and None mean different things: "it did not commit" versus
        "HEAD could not be read at all"."""
        self.assertIs(self._run(commits=False).head_moved, False)


if __name__ == "__main__":
    unittest.main()
