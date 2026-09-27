"""The learning loop has every part and nobody calls it.

    ledger.jsonl
      -> episodic.sync          index each line with a line-digest citation
      -> episodic.query         read the indexed facts back
      -> evolution.propose      group repeated, provenance-checked failures
      -> proposals.generate_candidate_proposals
      -> board proposal         draft, operator_review_required
      -> telegram render        a decision card
      -> operator approves      or it expires

Every step exists and is tested. `episodic.sync` is called only from tests,
and `generate_candidate_proposals` and `create_candidate_proposals` are called
from nowhere at all - the third module this session found written, tested,
and never reached. `telegram_control` imports `proposals` solely to *render*
proposals that nothing writes.

So the system cannot notice that it keeps failing the same way, and it keeps
failing the same way: of 52 failures in the first 82 runs, 19 were a model
answering without calling a tool, 8 were "the page keeps N of its own N
promises" and 4 were "no image, svg or background-image anywhere". Repetition
is exactly what `evolution.propose` is built to find, and it has never been
shown a single real run.

This wires it, with the safety properties the existing modules already
enforce left untouched: candidates are drafts, activation stays
`operator_review_required`, environment failures never qualify, and a
citation whose source line has changed is refused.
"""

from __future__ import annotations

import json
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import BoardTestCase  # noqa: E402

import board  # noqa: E402
import proposals  # noqa: E402


def _ledger(path: Path, rows) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                    encoding="utf-8")
    return path


REPEATED = [
    {"task_id": "a", "task_kind": "code", "failure_class": "logic",
     "outcome": "failed", "verify_exit": 7},
    {"task_id": "b", "task_kind": "code", "failure_class": "logic",
     "outcome": "failed", "verify_exit": 7},
]


class RefreshFromLedgerTest(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ledger = self.tmp / "ledger.jsonl"
        self.memory = self.tmp / "memory.db"
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()

    def _refresh(self, rows, **kwargs):
        _ledger(self.ledger, rows)
        return proposals.refresh_from_ledger(
            self.conn, ledger_path=self.ledger, memory_path=self.memory,
            workspace=self.workspace, **kwargs)

    def test_a_repeated_failure_becomes_a_proposal_on_the_board(self):
        created = self._refresh(REPEATED)
        self.assertEqual(len(created), 1)
        pending = board.pending_proposals_for_chat(self.conn, "42")
        self.assertEqual(len(pending), 1)

    def test_a_single_failure_proposes_nothing(self):
        self.assertEqual(self._refresh(REPEATED[:1]), ())

    def test_an_environment_failure_never_qualifies(self):
        rows = [dict(r, failure_class="environment") for r in REPEATED]
        self.assertEqual(self._refresh(rows), ())

    def test_running_twice_does_not_duplicate_the_proposal(self):
        """The daemon calls this on every idle tick."""
        self._refresh(REPEATED)
        self._refresh(REPEATED)
        self.assertEqual(len(board.pending_proposals_for_chat(self.conn, "42")), 1)

    def test_the_proposal_waits_for_a_decision(self):
        """Nothing this loop produces may act on its own."""
        self._refresh(REPEATED)
        row = dict(board.pending_proposals_for_chat(self.conn, "42")[0])
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["decided_at"])

    def test_nothing_is_activated_by_proposing_it(self):
        """The decisive check, not the wording of the card.

        A first version of this test asserted the string
        `operator_review_required`, which is the *candidate's* activation;
        `candidate_rule_mapping` turns it into a rule whose activation is
        `preflight_advice`. Reading the board for an activated rule asks the
        question that matters instead of a proxy for it.
        """
        self._refresh(REPEATED)
        row = dict(board.pending_proposals_for_chat(self.conn, "42")[0])
        self.assertIsNone(
            board.activated_procedural_rule(self.conn, str(row["id"])),
            "a candidate was activated without the operator",
        )

    def test_an_empty_ledger_is_not_an_error(self):
        self.ledger.write_text("", encoding="utf-8")
        self.assertEqual(
            proposals.refresh_from_ledger(
                self.conn, ledger_path=self.ledger, memory_path=self.memory,
                workspace=self.workspace), ())

    def test_a_missing_ledger_is_not_an_error(self):
        """An idle tick before the first run must not crash the daemon."""
        self.assertEqual(
            proposals.refresh_from_ledger(
                self.conn, ledger_path=self.tmp / "nope.jsonl",
                memory_path=self.memory, workspace=self.workspace), ())

    def test_it_never_raises_on_a_corrupt_ledger(self):
        """The daemon must survive a bad line; learning is not load-bearing."""
        self.ledger.write_text("{not json\n", encoding="utf-8")
        self.assertEqual(
            proposals.refresh_from_ledger(
                self.conn, ledger_path=self.ledger, memory_path=self.memory,
                workspace=self.workspace), ())


class TheDaemonCallsItWhenIdleTest(unittest.TestCase):
    """On idle, not on the critical path: a run must not wait for learning."""

    def test_serve_runs_the_idle_hook_when_there_is_no_work(self):
        import worker_daemon
        called = []
        worker_daemon.serve(
            tick=lambda: None,
            sleep=lambda _s: None,
            stop_requested=lambda: len(called) >= 2,
            on_idle=lambda: called.append(1),
            max_ticks=5,
        )
        self.assertTrue(called)

    def test_a_busy_tick_does_not_run_it(self):
        import worker_daemon
        called = []
        ticks = {"n": 0}

        def tick():
            ticks["n"] += 1
            return object()          # an attempt happened

        worker_daemon.serve(
            tick=tick, sleep=lambda _s: None,
            stop_requested=lambda: ticks["n"] >= 3,
            on_idle=lambda: called.append(1), max_ticks=5,
        )
        self.assertEqual(called, [])

    def test_a_raising_hook_never_stops_the_worker(self):
        import worker_daemon

        def boom():
            raise RuntimeError("learning blew up")

        ticks = {"n": 0}

        def tick():
            ticks["n"] += 1
            return None

        summary = worker_daemon.serve(
            tick=tick, sleep=lambda _s: None,
            stop_requested=lambda: ticks["n"] >= 3,
            on_idle=boom, max_ticks=5,
        )
        self.assertGreaterEqual(summary.ticks, 3)


if __name__ == "__main__":
    unittest.main()


class TheGroupingUsesAFieldTheLedgerHasTest(BoardTestCase):
    """`task_kind` is never written, so every failure was one group.

    `_signature` groups by (task_kind, failure_class, verify_exit) and
    defaults `task_kind` to "unknown". Nothing in `worker._entry` has ever
    emitted it, so on the real 82-entry ledger the loop drafted exactly one
    candidate citing 46 lines — "Candidate advice for unknown" — which is
    true, useless, and impossible to act on.

    `agent_role` was added to the ledger earlier this session for
    `lane_select`, and it is the grouping that carries meaning: a designer
    that keeps failing the taste gate is a different problem from a builder
    that keeps producing nothing.
    """

    def setUp(self) -> None:
        super().setUp()
        self.ledger = self.tmp / "ledger.jsonl"
        self.memory = self.tmp / "memory.db"
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()

    def _candidates(self, rows):
        import sys as _sys
        from memory import episodic, evolution
        _ledger(self.ledger, rows)
        conn = episodic.connect(self.memory)
        self.addCleanup(conn.close)
        episodic.sync(conn, self.ledger)
        return evolution.propose(
            episodic.query(conn, source_path=self.ledger))

    def _rows(self, *roles, verify_exit=7):
        return [
            {"task_id": f"t{i}", "agent_role": role, "failure_class": "logic",
             "outcome": "failed", "verify_exit": verify_exit}
            for i, role in enumerate(roles)
        ]

    def test_two_roles_failing_are_two_candidates_not_one(self):
        candidates = self._candidates(
            self._rows("designer", "designer",
                       "frontend_builder", "frontend_builder"))
        self.assertEqual(len(candidates), 2)

    def test_the_role_names_the_candidate(self):
        candidates = self._candidates(self._rows("designer", "designer"))
        self.assertEqual(candidates[0].task_kind, "designer")

    def test_one_failure_per_role_still_proposes_nothing(self):
        self.assertEqual(
            self._candidates(self._rows("designer", "frontend_builder")), ())

    def test_an_explicit_task_kind_still_wins(self):
        """The field the signature was written for keeps precedence."""
        rows = [dict(r, task_kind="code")
                for r in self._rows("designer", "designer")]
        self.assertEqual(self._candidates(rows)[0].task_kind, "code")

    def test_a_ledger_with_neither_field_still_groups(self):
        """Older lines predate `agent_role`; they must not vanish."""
        rows = [{"task_id": f"t{i}", "failure_class": "logic",
                 "outcome": "failed", "verify_exit": 7} for i in range(2)]
        self.assertEqual(self._candidates(rows)[0].task_kind, "unknown")


class TheRuleIsScopedToTheWorkspaceItCameFromTest(BoardTestCase):
    """A rule carries a workspace, and it has to be a real one.

    `procedural.rule_from_mapping` requires an absolute `workspace`, and the
    card renders it as "Candidate advice for <kind> in <workspace.name>". The
    first wiring passed the worker daemon's `--runs-dir` — `~/.tri-ai/runs` —
    because that was the only path the daemon had. Every rule would have been
    scoped to the runs directory, which is not a workspace any task runs in,
    so no rule could ever match the thing it was derived from.

    The ledger already records the workspace per line as `repo`. Partitioning
    on it also scopes the evidence correctly: advice derived from failures in
    one project should not be offered for another, and the rule schema has no
    way to express a cross-workspace pattern anyway.
    """

    def setUp(self) -> None:
        super().setUp()
        self.ledger = self.tmp / "ledger.jsonl"
        self.memory = self.tmp / "memory.db"
        self.a = self.tmp / "project-a"
        self.b = self.tmp / "project-b"
        self.a.mkdir()
        self.b.mkdir()

    def _refresh(self, rows):
        _ledger(self.ledger, rows)
        return proposals.refresh_from_ledger(
            self.conn, ledger_path=self.ledger, memory_path=self.memory,
            workspace=self.tmp / "fallback")

    def _rows(self, repo, n=2, role="designer"):
        return [
            {"task_id": f"{Path(repo).name}-{i}", "repo": str(repo),
             "agent_role": role, "failure_class": "logic",
             "outcome": "failed", "verify_exit": 7}
            for i in range(n)
        ]

    def _workspaces(self):
        out = []
        for row in board.pending_proposals_for_chat(self.conn, "42"):
            rule = (dict(row).get("payload") or {}).get("rule") or {}
            out.append(rule.get("workspace"))
        return out

    def test_the_rule_names_the_workspace_the_failures_happened_in(self):
        self._refresh(self._rows(self.a))
        self.assertEqual(self._workspaces(), [str(self.a.resolve())])

    def test_two_workspaces_give_two_separately_scoped_rules(self):
        self._refresh(self._rows(self.a) + self._rows(self.b))
        self.assertEqual(sorted(self._workspaces()),
                         sorted([str(self.a.resolve()), str(self.b.resolve())]))

    def test_one_failure_in_each_of_two_workspaces_proposes_nothing(self):
        """Partitioning must not let two unrelated single failures combine."""
        self.assertEqual(
            self._refresh(self._rows(self.a, n=1) + self._rows(self.b, n=1)), ())

    def test_a_line_with_no_repo_falls_back_to_the_supplied_workspace(self):
        rows = [{"task_id": f"t{i}", "agent_role": "designer",
                 "failure_class": "logic", "outcome": "failed",
                 "verify_exit": 7} for i in range(2)]
        self._refresh(rows)
        self.assertEqual(self._workspaces(),
                         [str((self.tmp / "fallback").resolve())])


class TheDaemonWiresItByDefaultTest(unittest.TestCase):
    """Found by Codex reviewing this change set.

    `worker.run_once(ledger_path=None)` falls back to `ledger.ledger_path()`
    and writes there, but `_learning_pass(ledger_path=None)` returned None and
    installed no hook. So a daemon started without `--ledger` ledgers its runs
    and never learns from them - the two halves disagreed about what "no
    ledger given" means.

    The deployed supervisor does pass `--ledger`, so production was wired;
    this is the default path, and the asymmetry is the kind that survives
    until someone changes the supervisor.
    """

    def test_the_hook_exists_without_an_explicit_ledger(self):
        import worker_daemon
        hook = worker_daemon._learning_pass(
            None, ledger_path=None, memory_path=None, workspace=None)
        self.assertIsNotNone(
            hook, "a daemon with no --ledger still ledgers, so it must learn")

    def test_it_uses_the_same_default_the_worker_writes_to(self):
        import ledger as ledger_module
        import worker_daemon
        seen = {}

        def fake(conn, *, ledger_path, memory_path, workspace, **kw):
            seen["ledger"] = Path(ledger_path)
            return ()

        import proposals as proposals_module
        with mock.patch.object(proposals_module, "refresh_from_ledger",
                               side_effect=fake):
            worker_daemon._learning_pass(
                None, ledger_path=None, memory_path=None, workspace=None)()
        self.assertEqual(seen.get("ledger"),
                         Path(ledger_module.ledger_path()))
