"""A finished run must become something the Brain can answer questions about.

Codex's last message before its limit named this: "making Cortex retain real
run evidence in its private brain, so completed work is queryable rather than
only a moment on the animation."

The gap is the one this project keeps producing. `memory/brain.py` is a
complete kernel — `capture`, `search`, `context_pack`, `link`, dedup,
credential refusal, trust labels — and its only caller is Telegram's
`/remember`. Nothing captures a run. So two verified end-to-end passes (run
101 on the desktop, run 43 on the laptop) left a ledger line, some files, and
a few seconds of animation, and the Cortex brain panel still reads
0 MEMORIES.

What a captured run must hold, and what it must not:

* enough to answer "what has this system actually built?" later — title,
  outcome, the verify verdict, what landed on disk;
* provenance back to the run that produced it, so the claim is checkable;
* never a credential — `brain.capture` already refuses those and that
  refusal must not be bypassed here;
* never a machine path. The Cortex index is deliberately path-free
  (`5aea63c`) because this brain is shown on a dashboard; a run memory has
  to hold to the same rule.

Capture is also not allowed to break a run. A brain write failing after the
gate has already passed would turn a delivered result into a failure, which
inverts the whole point of the verify gate.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memory import brain  # noqa: E402


class CaptureRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = brain.connect(
            Path(tempfile.mkdtemp(prefix="triai-brain-")) / "brain.db")
        self.addCleanup(self.conn.close)

    def _capture(self, **kw):
        fields = dict(
            task_id="t_abc123", run_id=101,
            title="Field guide to four northern Indian garden birds",
            outcome="passed", verify_summary="kept 22 of 22 promises from 3 references",
            artifacts=("field-guide.html", "BRIEF.md"),
            agent_role="designer", model="auto/best-coding",
        )
        fields.update(kw)
        return brain.capture_run(self.conn, **fields)

    def test_a_passed_run_becomes_a_brain_item(self):
        item = self._capture()
        self.assertTrue(item.item_id)
        self.assertEqual(brain.stats(self.conn).item_count, 1)

    def test_it_is_findable_by_what_was_built(self):
        """The whole point: queryable later, not a moment on an animation."""
        self._capture()
        hits = brain.search(self.conn, "field guide")
        self.assertTrue(hits, "a captured run must be searchable")

    def test_the_outcome_and_verdict_are_recorded(self):
        item = self._capture()
        blob = f"{item.title} {item.body}"
        self.assertIn("passed", blob)
        self.assertIn("22 of 22", blob)

    def test_the_artifacts_are_named(self):
        item = self._capture()
        self.assertIn("field-guide.html", item.body)

    def test_provenance_points_back_at_the_run(self):
        item = self._capture()
        self.assertIn("t_abc123", f"{item.source}{item.body}")
        self.assertIn("101", f"{item.source}{item.body}")

    def test_the_same_run_captured_twice_is_one_item(self):
        """The worker may retry its own bookkeeping; the brain dedups."""
        first = self._capture()
        second = self._capture()
        self.assertEqual(first.item_id, second.item_id)
        self.assertEqual(brain.stats(self.conn).item_count, 1)

    def test_a_failed_run_is_captured_too(self):
        """What did not work is evidence. 19 of 52 failures were one model
        never calling a tool, and that is worth being able to ask about."""
        item = self._capture(outcome="failed",
                             verify_summary="this run changed nothing")
        self.assertIn("failed", f"{item.title} {item.body}")

    # -- the boundaries ---------------------------------------------------

    def test_a_machine_path_never_enters_the_brain(self):
        """The Cortex index is deliberately path-free; so is this."""
        item = self._capture(artifacts=(
            r"C:\Users\Ardit II\tri-ai-sandbox\field-guide.html",))
        self.assertNotIn("C:\\", item.body)
        self.assertNotIn("Ardit II", item.body)
        self.assertIn("field-guide.html", item.body)

    def test_a_credential_shaped_summary_is_refused_not_stored(self):
        with self.assertRaises(ValueError):
            self._capture(verify_summary="token=ghp_abcdefghijklmnopqrstuvwxyz0123456789")
        self.assertEqual(brain.stats(self.conn).item_count, 0)

    def test_it_is_captured_unreviewed(self):
        """Captured evidence informs a prompt; it never becomes a rule by
        itself. That is the existing Brain contract and this must not relax
        it."""
        item = self._capture()
        self.assertEqual(item.trust, "unreviewed")


if __name__ == "__main__":
    unittest.main()


# --- and the wiring, against a real worker attempt --------------------------

import json  # noqa: E402
import subprocess  # noqa: E402
from unittest import mock  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import BoardTestCase  # noqa: E402

import board  # noqa: E402
import executor  # noqa: E402
import worker  # noqa: E402


class TheWorkerRemembersItsRunsTest(BoardTestCase):
    """`capture_run` existing is not the same as anything calling it.

    That distinction has cost this project three modules so far -
    `lane_select`, the cartographer's runtime path, and the whole learning
    loop were each written, tested, and reached by nothing. The brain kernel
    is the fourth: complete since it was built, called only by `/remember`.
    """

    def setUp(self) -> None:
        super().setUp()
        self.ledger = self.tmp / "ledger.jsonl"
        self.runs = self.tmp / "runs"
        self.brain_path = self.tmp / "brain" / "brain.db"
        self.repo = self.tmp / "workspace"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "config", "user.email", "t@e.com"],
                       cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=self.repo, check=True)
        (self.repo / "m.txt").write_text("b\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.repo, check=True)

    def _run(self, *, passing=True):
        task_id = board.create_task(
            self.conn, title="Build the field guide", prompt="build it",
            verify_command='python -c "pass"' if passing
            else 'python -c "import sys;sys.exit(1)"',
            repo=self.repo, verify_timeout=60, agent_role="designer")
        agent = executor.AgentResult(
            0, "done\n", 1.0, model="auto/best-coding",
            model_source="usage_file", api_calls=6)
        verify = executor.VerifyResult(
            "passed" if passing else "failed", 0 if passing else 1,
            "kept 22 of 22 promises", 0.1)
        claimed = self.kb.claim_task(self.conn, task_id,
                                     claimer=worker.worker_id())
        with mock.patch.object(executor, "run_agent", return_value=agent), \
             mock.patch.object(executor, "run_verify", return_value=verify):
            worker.execute_task(self.conn, claimed, ledger_path=self.ledger,
                                runs_root=self.runs, brain_path=self.brain_path)
        return task_id

    def _memories(self):
        if not self.brain_path.exists():
            return ()
        conn = brain.connect_read_only(self.brain_path)
        try:
            return brain.search(conn, "field guide")
        finally:
            conn.close()

    def test_a_passing_run_is_remembered(self):
        self._run(passing=True)
        self.assertTrue(self._memories(), "the run left no trace in the brain")

    def test_a_failing_run_is_remembered_too(self):
        self._run(passing=False)
        self.assertTrue(self._memories())

    def test_the_memory_names_the_outcome(self):
        self._run(passing=True)
        self.assertIn("passed", " ".join(m.title for m in self._memories()))

    def test_no_brain_path_is_not_an_error(self):
        """Every existing caller omits it; the default must stay silent."""
        task_id = board.create_task(
            self.conn, title="x", prompt="x", verify_command='python -c "pass"',
            repo=self.repo, verify_timeout=60)
        agent = executor.AgentResult(0, "done\n", 1.0, model="m",
                                     model_source="usage_file", api_calls=4)
        verify = executor.VerifyResult("passed", 0, "ok", 0.1)
        claimed = self.kb.claim_task(self.conn, task_id,
                                     claimer=worker.worker_id())
        with mock.patch.object(executor, "run_agent", return_value=agent), \
             mock.patch.object(executor, "run_verify", return_value=verify):
            att = worker.execute_task(self.conn, claimed,
                                      ledger_path=self.ledger,
                                      runs_root=self.runs)
        self.assertEqual(att.outcome, "passed")

    def test_a_broken_brain_never_fails_a_delivered_run(self):
        """A bookkeeping write failing after the gate passed would turn a
        delivered result into a failure. That inverts the verify gate."""
        self.brain_path.parent.mkdir(parents=True, exist_ok=True)
        self.brain_path.write_text("not a database", encoding="utf-8")
        task_id = board.create_task(
            self.conn, title="Build the field guide", prompt="build it",
            verify_command='python -c "pass"', repo=self.repo,
            verify_timeout=60, agent_role="designer")
        agent = executor.AgentResult(0, "done\n", 1.0, model="m",
                                     model_source="usage_file", api_calls=6)
        verify = executor.VerifyResult("passed", 0, "ok", 0.1)
        claimed = self.kb.claim_task(self.conn, task_id,
                                     claimer=worker.worker_id())
        with mock.patch.object(executor, "run_agent", return_value=agent), \
             mock.patch.object(executor, "run_verify", return_value=verify):
            att = worker.execute_task(self.conn, claimed,
                                      ledger_path=self.ledger,
                                      runs_root=self.runs,
                                      brain_path=self.brain_path)
        self.assertEqual(att.outcome, "passed")
