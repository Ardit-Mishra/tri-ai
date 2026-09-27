"""Let a task name the lane it runs on, and record what actually served it.

Tri-AI has never chosen a model. `run_agent` built
`argv = [hermes, "-z", prompt]` plus `--usage-file`, and read the model back
afterwards for ledger honesty only. So `lane_select` — 300 lines, tested,
declared in the safety closure — has nothing to act through, and the most
common failure in the system has no lever.

That failure is now understood. Nineteen of fifty-two failures are a model
that answered once and never called a tool, and the configuration explanation
is dead: `hermes-cli`, the enabled toolset, *is* `_HERMES_CORE_TOOLS`, and
`terminal`, `write_file` and `patch` are in it and documented as never
deferred. The tools were always there. A one-call turn is the model.

Two deliberate limits.

**No column is added.** The Hermes kernel already carries `model_override`
and `provider_override` per task, for its own `kanban set-model`. board.py's
rule is that the kernel is used, never edited, so this reads those.

**No default changes.** A task with no override produces exactly today's
argv, and Hermes routes as it does now. This is the capability plus the
record, not a policy — and the record is what finally makes the missing
comparison possible: no task in 82 runs has ever been run on two lanes, which
is why "auto/best-free passes 7/7" cannot be separated from "auto/best-free
only ever got easy tasks".
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import BoardTestCase  # noqa: E402

import board  # noqa: E402
import executor  # noqa: E402
import ledger as ledger_module  # noqa: E402
import worker  # noqa: E402


class ArgvCarriesTheLaneTest(unittest.TestCase):
    """What reaches the process, checked at the process boundary."""

    def _argv(self, **kwargs):
        seen = {}

        class _Proc:
            def __init__(self):
                import io
                self.stdout = io.StringIO("")
                self.returncode = 0

            def wait(self, timeout=None):
                return 0

            def communicate(self, timeout=None):
                return "", ""

        class _Containment:
            def __init__(self):
                self.proc = _Proc()

            def terminate_tree(self, grace=None):
                return [], []

            def close(self):
                pass

        def spy(command, **popen_kwargs):
            seen["argv"] = list(command)
            return _Containment()

        with mock.patch.object(executor, "hermes_bin",
                               return_value=Path("hermes")), \
             mock.patch.object(executor, "spawn_contained", side_effect=spy):
            executor.run_agent(Path(tempfile.gettempdir()), "build it",
                               timeout=30, **kwargs)
        return seen["argv"]

    def test_no_override_leaves_the_command_exactly_as_it_was(self):
        argv = self._argv()
        self.assertNotIn("-m", argv)
        self.assertNotIn("--provider", argv)

    def test_a_model_is_passed_through(self):
        argv = self._argv(model="deskollama/qwen2.5-coder:7b")
        self.assertIn("-m", argv)
        self.assertEqual(argv[argv.index("-m") + 1],
                         "deskollama/qwen2.5-coder:7b")

    def test_a_provider_is_passed_through(self):
        argv = self._argv(provider="ollama")
        self.assertEqual(argv[argv.index("--provider") + 1], "ollama")

    def test_a_blank_override_is_treated_as_absent(self):
        """An empty string in the column must not become `-m ''`."""
        argv = self._argv(model="   ", provider="")
        self.assertNotIn("-m", argv)
        self.assertNotIn("--provider", argv)

    def test_the_prompt_is_still_the_second_argument(self):
        """Ordering matters: `-z` takes the prompt positionally."""
        argv = self._argv(model="x")
        self.assertEqual(argv[1], "-z")
        self.assertIn("build it", argv[2])

    def test_the_request_is_reported_back(self):
        """What we asked for, separate from what answered."""
        class _Proc:
            def __init__(self):
                import io
                self.stdout = io.StringIO("")
                self.returncode = 0

            def wait(self, timeout=None):
                return 0

            def communicate(self, timeout=None):
                return "", ""

        class _Containment:
            def __init__(self):
                self.proc = _Proc()

            def terminate_tree(self, grace=None):
                return [], []

            def close(self):
                pass

        with mock.patch.object(executor, "hermes_bin",
                               return_value=Path("hermes")), \
             mock.patch.object(executor, "spawn_contained",
                               side_effect=lambda *a, **k: _Containment()):
            result = executor.run_agent(
                Path(tempfile.gettempdir()), "build it", timeout=30,
                model="lane-a")
        self.assertEqual(result.model_requested, "lane-a")


class TheBoardCarriesTheChoiceTest(BoardTestCase):
    """Read from the kernel's own columns, not a new one."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.tmp / "workspace"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

    def _task(self, **kwargs):
        return board.create_task(
            self.conn, title="t", prompt="p",
            verify_command="python -c \"pass\"", repo=self.repo,
            verify_timeout=60, **kwargs)

    def test_a_task_without_an_override_reports_none(self):
        spec = board.verify_spec(self.conn, self._task())
        self.assertIsNone(spec.get("model_override"))
        self.assertIsNone(spec.get("provider_override"))

    def test_the_kernels_own_column_is_read(self):
        task_id = self._task()
        self.conn.execute(
            "UPDATE tasks SET model_override = ?, provider_override = ? "
            "WHERE id = ?", ("deskollama/qwen2.5-coder:7b", "ollama", task_id))
        self.conn.commit()
        spec = board.verify_spec(self.conn, task_id)
        self.assertEqual(spec["model_override"], "deskollama/qwen2.5-coder:7b")
        self.assertEqual(spec["provider_override"], "ollama")


class TheLedgerRecordsBothTest(BoardTestCase):
    """Asked-for and served, so a substitution is visible."""

    def setUp(self) -> None:
        super().setUp()
        self.ledger = self.tmp / "ledger.jsonl"
        self.runs = self.tmp / "runs"
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

    def _run(self, *, requested, served):
        task_id = board.create_task(
            self.conn, title="t", prompt="p",
            verify_command="python -c \"pass\"", repo=self.repo,
            verify_timeout=60, agent_role="builder")
        if requested:
            self.conn.execute(
                "UPDATE tasks SET model_override = ? WHERE id = ?",
                (requested, task_id))
            self.conn.commit()

        captured = {}

        def fake_agent(repo, prompt, **kwargs):
            captured["model"] = kwargs.get("model")
            return executor.AgentResult(
                0, "done\n", 1.0, model=served, model_source="usage_file",
                api_calls=6, model_requested=kwargs.get("model"))

        verify = executor.VerifyResult("passed", 0, "ok", 0.1)
        claimed = self.kb.claim_task(self.conn, task_id,
                                     claimer=worker.worker_id())
        with mock.patch.object(executor, "run_agent", side_effect=fake_agent), \
             mock.patch.object(executor, "run_verify", return_value=verify):
            worker.execute_task(self.conn, claimed, ledger_path=self.ledger,
                                runs_root=self.runs)
        return captured, ledger_module.read_entries(self.ledger)[-1]

    def test_the_override_reaches_run_agent(self):
        captured, _ = self._run(requested="lane-a", served="lane-a")
        self.assertEqual(captured["model"], "lane-a")

    def test_no_override_passes_nothing(self):
        captured, _ = self._run(requested=None, served="auto/best-coding")
        self.assertIsNone(captured["model"])

    def test_both_names_are_on_the_ledger_line(self):
        _, entry = self._run(requested="lane-a", served="lane-a")
        self.assertEqual(entry["model_requested"], "lane-a")
        self.assertEqual(entry["model"], "lane-a")

    def test_a_substitution_is_visible(self):
        """A router that serves something else must not look like a hit.

        This is the comparison `auto/*` aliases make impossible today: the
        usage file reports the alias, so nothing records which model actually
        answered.
        """
        _, entry = self._run(requested="lane-a", served="lane-b")
        self.assertEqual(entry["model_requested"], "lane-a")
        self.assertEqual(entry["model"], "lane-b")


if __name__ == "__main__":
    unittest.main()


class ASilentSubstitutionIsNotSilentTest(BoardTestCase):
    """A lane override that is ignored must not look like one that worked.

    Measured against the real binary on the desktop runtime:

        -m auto/best-free  --provider custom  -> served auto/smart
        -m qwen2.5-coder:7b                   -> served auto/smart
        -m this-model-does-not-exist          -> served auto/smart, exit 0

    Every request fell back, a model name that does not exist produced no
    error at all, and only the ledger pair `model_requested` / `model` shows
    it. Recording is not enough: a pinned lane silently not being used
    defeats the entire purpose of pinning it, and the operator would read the
    run as evidence about a lane that never ran.
    """

    def setUp(self) -> None:
        super().setUp()
        self.ledger = self.tmp / "ledger.jsonl"
        self.runs = self.tmp / "runs"
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

    def _run(self, *, requested, served, passing=True):
        task_id = board.create_task(
            self.conn, title="t", prompt="p",
            verify_command="python -c \"pass\"", repo=self.repo,
            verify_timeout=60, agent_role="builder")
        if requested:
            self.conn.execute(
                "UPDATE tasks SET model_override = ? WHERE id = ?",
                (requested, task_id))
            self.conn.commit()

        def fake_agent(repo, prompt, **kwargs):
            return executor.AgentResult(
                0, "done\n", 1.0, model=served, model_source="usage_file",
                api_calls=6, model_requested=kwargs.get("model"))

        verify = executor.VerifyResult(
            "passed" if passing else "failed", 0 if passing else 1, "ok", 0.1)
        claimed = self.kb.claim_task(self.conn, task_id,
                                     claimer=worker.worker_id())
        with mock.patch.object(executor, "run_agent", side_effect=fake_agent), \
             mock.patch.object(executor, "run_verify", return_value=verify):
            worker.execute_task(self.conn, claimed, ledger_path=self.ledger,
                                runs_root=self.runs)
        return "\n".join(p.read_text(encoding="utf-8")
                         for p in self.runs.rglob("verify.log"))

    def test_a_substitution_is_written_where_the_run_is_read(self):
        log = self._run(requested="auto/best-free", served="auto/smart")
        self.assertIn("auto/best-free", log)
        self.assertIn("auto/smart", log)

    def test_the_word_used_is_one_an_operator_would_search_for(self):
        log = self._run(requested="auto/best-free", served="auto/smart")
        self.assertIn("lane", log.casefold())

    def test_an_honoured_override_says_nothing(self):
        log = self._run(requested="auto/smart", served="auto/smart")
        self.assertNotIn("auto/smart was requested", log)

    def test_no_override_says_nothing(self):
        log = self._run(requested=None, served="auto/best-coding")
        self.assertNotIn("requested", log.casefold())

    def test_it_is_said_on_a_failing_run_too(self):
        """A failed run on the wrong lane is worse, not better: it is about to
        be read as evidence against a lane that never served it."""
        log = self._run(requested="auto/best-free", served="auto/smart",
                        passing=False)
        self.assertIn("auto/best-free", log)

    def test_a_substituted_lane_never_reaches_verification(self):
        """A pin is a runtime requirement, not merely a ledger annotation."""
        task_id = board.create_task(
            self.conn, title="t", prompt="p",
            verify_command="python -c \"pass\"", repo=self.repo,
            verify_timeout=60, agent_role="builder")
        self.conn.execute(
            "UPDATE tasks SET model_override = ? WHERE id = ?",
            ("requested-model", task_id))
        self.conn.commit()

        verified = []

        def fake_agent(*_args, **kwargs):
            return executor.AgentResult(
                0, "done\\n", 1.0, model="fallback-model",
                model_source="usage_file", api_calls=6,
                model_requested=kwargs.get("model"))

        def fake_verify(*_args, **_kwargs):
            verified.append(True)
            return executor.VerifyResult("passed", 0, "ok", 0.1)

        claimed = self.kb.claim_task(self.conn, task_id,
                                     claimer=worker.worker_id())
        with mock.patch.object(executor, "run_agent", side_effect=fake_agent), \
             mock.patch.object(executor, "run_verify", side_effect=fake_verify):
            attempt = worker.execute_task(
                self.conn, claimed, ledger_path=self.ledger,
                runs_root=self.runs)

        self.assertEqual(verified, [])
        self.assertIn("environment", attempt.outcome)
        self.assertEqual(attempt.entry["failure_class"], "environment")
        self.assertIn("requested-model", attempt.entry["reason"])
        self.assertIn("fallback-model", attempt.entry["reason"])
