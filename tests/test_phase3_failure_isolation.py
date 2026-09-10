"""Phase 3 Slice 3: linked-graph failure isolation.

Builds a real DAG through ``task_links`` (not a flat task list), completes
root, then dispatches the remaining ready nodes with the measured cap:

    root
    |- failing-parent
    |  `- blocked-descendant
    |- passing-sibling-b
    `- passing-sibling-c

``failing-parent`` carries a deliberate non-zero verify command; both siblings
carry passing commands and no dependency on the failing branch.  The test
proves, through the REAL worker execution path (``worker.execute_task``,
claim -> precheck -> agent -> verify -> accept/revert, with the ledger written
by the worker), that:

- the failing attempt is ledgered with ``verify_outcome='failed'`` and then
  follows the existing bounded retry / kernel circuit-breaker state machine
  (two consecutive failures -> ``blocked`` + ``gave_up``);
- ``blocked-descendant`` never becomes claimable, because its parent never
  reaches ``done`` — the kernel's single enforcement point demotes a ready
  child with undone parents back to ``todo`` at claim time;
- both independent siblings reach ``done`` in the SAME dispatcher run, with
  ledger entries carrying valid worker/run identities; and
- no graph-wide failure flag or dispatcher early-return prevented the siblings
  (proven affirmatively here, and reversely by the deliberately broken
  dispatcher test).

The deliberately broken dispatcher launches one child at a time and stops at
the first failed child; the sibling-completion assertion fails under it, which
guards against a test whose success came from completing siblings before the
failure path existed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import board  # noqa: E402
import dispatcher  # noqa: E402
import executor  # noqa: E402
import ledger  # noqa: E402
import worker  # noqa: E402
from dispatcher import WorkerResult  # noqa: E402
from support import BoardTestCase  # noqa: E402


PASS_CMD = "python -c \"import sys; sys.exit(0)\""
FAIL_CMD = "python -c \"import sys; sys.exit(7)\""  # the deliberate non-zero exit


def _write_concurrency(path: Path, cap: int) -> None:
    """A minimal concurrency_results.json exposing the given measured cap."""
    runs = [
        {
            "concurrency": c,
            "wall_seconds": 2.88 * c,
            "completed": c,
            "failed": 0,
            "failures": [],
        }
        for c in range(1, cap + 1)
    ]
    path.write_text(json.dumps({"runs": runs}, indent=2), encoding="utf-8")


def _init_repo(path: Path) -> Path:
    """A clean, committed git repo — required by the worker's precheck/revert."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], capture_output=True, timeout=30)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "seed", "--allow-empty"],
        capture_output=True, timeout=30,
    )
    return path


class LinkedGraphFailureIsolation(BoardTestCase):
    """One real git repo per task; the executor agent is the only double."""

    def setUp(self) -> None:
        super().setUp()
        self.ledger = self.tmp / "ledger.jsonl"
        self.runs = self.tmp / "runs"
        self.runs.mkdir()
        self.cap_cfg = self.tmp / "concurrency.json"
        _write_concurrency(self.cap_cfg, cap=4)

    # -- fixtures ---------------------------------------------------------

    def _seed_dag(self, *, failing_priority: int = 0) -> dict[str, str]:
        """Persist the Slice 3 DAG through real task_links; return task ids.

        ``root`` starts ``ready`` (no parents); every child starts ``todo`` and
        is promoted to ``ready`` only when its own parents are ``done``.
        """
        ids: dict[str, str] = {}
        ids["root"] = board.create_task(
            self.conn, title="root", prompt="seed the graph",
            verify_command=PASS_CMD, verify_timeout=30,
            repo=_init_repo(self.tmp / "ws-root"),
        )
        ids["failing-parent"] = board.create_task(
            self.conn, title="failing-parent", prompt="deliberately fails",
            verify_command=FAIL_CMD, verify_timeout=30,
            repo=_init_repo(self.tmp / "ws-failing-parent"),
            parents=[ids["root"]],
            priority=failing_priority,
        )
        ids["blocked-descendant"] = board.create_task(
            self.conn, title="blocked-descendant", prompt="must stay unclaimable",
            verify_command=PASS_CMD, verify_timeout=30,
            repo=_init_repo(self.tmp / "ws-blocked-descendant"),
            parents=[ids["failing-parent"]],
        )
        ids["sibling-b"] = board.create_task(
            self.conn, title="passing-sibling-b", prompt="passes",
            verify_command=PASS_CMD, verify_timeout=30,
            repo=_init_repo(self.tmp / "ws-sibling-b"),
            parents=[ids["root"]],
        )
        ids["sibling-c"] = board.create_task(
            self.conn, title="passing-sibling-c", prompt="passes",
            verify_command=PASS_CMD, verify_timeout=30,
            repo=_init_repo(self.tmp / "ws-sibling-c"),
            parents=[ids["root"]],
        )
        return ids

    def _make_launcher(self, board_path: Path | str):
        """A worker launcher that mirrors a real ``--once`` worker for exactly
        the dispatched task: claim -> execute_task -> report exit status.

        ``executor.run_agent`` is the single double (no real Hermes); the
        verify command is executed for real, so a task's outcome follows its
        deliberate verify command, not the test's intent.
        """
        def launch(task_id: str) -> WorkerResult:
            conn = board.connect(Path(board_path))
            try:
                kb = board.kanban()
                claimed = kb.claim_task(conn, task_id, claimer=ledger.worker_id())
                if claimed is None:
                    # A real worker that finds nothing to do exits 0. Here this
                    # is the kernel refusing a child whose parents are undone.
                    return WorkerResult(
                        task_id=task_id, exit_code=0,
                        error="claim refused (parents not done)",
                    )
                try:
                    attempt = worker.execute_task(
                        conn, claimed, ledger_path=self.ledger, runs_root=self.runs,
                    )
                except Exception as exc:  # noqa: BLE001 - report, don't crash the batch
                    return WorkerResult(
                        task_id=task_id, exit_code=1, error=f"crash: {exc!r}",
                    )
                return WorkerResult(
                    task_id=task_id,
                    exit_code=0 if attempt.outcome == "passed" else 1,
                    error=attempt.outcome,
                )
            finally:
                conn.close()
        return launch

    def _run_dispatch(self, launcher, *, max_waves: int | None) -> dispatcher.DispatchResult:
        with mock.patch.object(
            executor, "run_agent",
            return_value=executor.AgentResult(0, "agent ran\n", 1.0),
        ):
            return dispatcher.dispatch(
                launcher=launcher,
                board_path=self.db_path,
                concurrency_source=self.cap_cfg,
                ledger_path=self.ledger,
                runs_root=self.runs,
                max_waves=max_waves,
            )

    # -- shared assertions ------------------------------------------------

    def _assert_valid_identity(self, entry: dict) -> None:
        """A ledger entry must carry a host:pid worker and a kernel run id."""
        self.assertIsNotNone(entry.get("worker"), "worker identity missing")
        self.assertRegex(entry["worker"], r"^[^:]+:\d+$")
        self.assertIsNotNone(entry.get("run_id"), "kernel-owned run id missing")

    def _assert_siblings_are_done(self, ids: dict[str, str]) -> None:
        """Both independent siblings are done with exactly one passed entry."""
        entries = ledger.read_entries(self.ledger)
        for sid in (ids["sibling-b"], ids["sibling-c"]):
            self.assertEqual(
                self.task_row(sid)["status"], "done",
                f"sibling {sid} must have reached done",
            )
            mine = [e for e in entries if e["task_id"] == sid]
            self.assertEqual(len(mine), 1, f"sibling {sid} must have one ledger entry")
            self.assertEqual(mine[0]["verify_outcome"], "passed")
            self.assertEqual(mine[0]["verify_exit"], 0)
            self._assert_valid_identity(mine[0])

    # -- the proof --------------------------------------------------------

    def test_failing_branch_blocks_only_its_own_descendant(self):
        ids = self._seed_dag()
        launcher = self._make_launcher(self.db_path)
        result = self._run_dispatch(launcher, max_waves=8)

        # Root — the graph's source — completes first through the dispatcher.
        self.assertEqual(self.task_row(ids["root"])["status"], "done")

        # The failing branch follows the kernel's bounded retry/circuit-breaker
        # state machine: two consecutive failed attempts -> blocked + gave_up.
        self.assertEqual(
            self.task_row(ids["failing-parent"])["status"], "blocked",
            "circuit breaker must trip after the second consecutive failure",
        )
        self.assertIn("gave_up", self.event_kinds(ids["failing-parent"]))

        # blocked-descendant never becomes claimable: its parent is never done.
        self.assertEqual(
            self.task_row(ids["blocked-descendant"])["status"], "todo",
            "descendant must stay dependency-blocked, never ready",
        )
        kb = board.kanban()
        self.assertIsNone(
            kb.claim_task(self.conn, ids["blocked-descendant"], claimer=ledger.worker_id()),
            "the kernel's single enforcement point must refuse the claim",
        )

        # Independent siblings reach done in this same dispatcher run.
        self._assert_siblings_are_done(ids)

        # The failed attempts are ledgered with the deliberate non-zero exit.
        entries = ledger.read_entries(self.ledger)
        fp_rows = [e for e in entries if e["task_id"] == ids["failing-parent"]]
        self.assertEqual(len(fp_rows), 2, "both failed attempts must be ledgered")
        for e in fp_rows:
            self.assertEqual(e["outcome"], "failed")
            self.assertEqual(e["verify_outcome"], "failed")
            self.assertEqual(e["verify_exit"], 7, "the deliberate non-zero verify command")
            self._assert_valid_identity(e)

        root_rows = [e for e in entries if e["task_id"] == ids["root"]]
        self.assertEqual(len(root_rows), 1)
        self.assertEqual(root_rows[0]["verify_outcome"], "passed")
        self._assert_valid_identity(root_rows[0])

        # The DispatchResult is itself evidence of no early return: the failed
        # child was re-dispatched (wave re-read picks the reclaimed task up),
        # and the siblings' passing results sit beside the failures.
        self.assertEqual(len(result.results), 5,
                         "root + two failing-parent attempts + two siblings")
        fp_results = [r for r in result.results if r.task_id == ids["failing-parent"]]
        self.assertEqual(len(fp_results), 2, "the failed child is retried")
        self.assertTrue(all(r.exit_code != 0 for r in fp_results))
        sib_results = [r for r in result.results if r.task_id in
                       (ids["sibling-b"], ids["sibling-c"])]
        self.assertEqual(len(sib_results), 2)
        self.assertTrue(all(r.exit_code == 0 for r in sib_results),
                        "a sibling failure result without a board failure is "
                        "detectable here")

    # -- the negative control ---------------------------------------------

    def test_broken_dispatcher_that_stops_on_first_failure_leaves_siblings_ready(self):
        """A deliberately broken dispatcher must make the sibling-completion
        assertion fail — the positive result must not be hiding a dispatcher
        that already completed the siblings before the failure path ran."""
        ids = self._seed_dag(failing_priority=100)  # failing-parent sorts first
        launcher = self._make_launcher(self.db_path)

        def broken_dispatch() -> None:
            """Deterministic, deliberately wrong dispatcher: launch one ready
            task at a time and STOP at the first failed child. Siblings behind
            the failing branch are never launched."""
            conn = board.connect(Path(self.db_path))
            try:
                dispatched: set[str] = set()
                while True:
                    ready = board.ready_tasks(conn)
                    pending = [t for t in ready if t["id"] not in dispatched]
                    if not pending:
                        break
                    tid = pending[0]["id"]
                    dispatched.add(tid)
                    outcome = launcher(tid)
                    if outcome.exit_code != 0:
                        # BROKEN: it gives up at the first failure instead of
                        # letting the independent branch finish.
                        return
            finally:
                conn.close()

        with mock.patch.object(
            executor, "run_agent",
            return_value=executor.AgentResult(0, "agent ran\n", 1.0),
        ):
            broken_dispatch()

        # Root completes, failing-parent fails once (retryable, not yet blocked).
        self.assertEqual(self.task_row(ids["root"])["status"], "done")
        self.assertEqual(
            self.task_row(ids["failing-parent"])["status"], "ready",
            "a single failure is retryable — nothing has tripped yet",
        )
        # The sibling-completion assertion is exactly what fails.
        with self.assertRaises(AssertionError):
            self._assert_siblings_are_done(ids)
        for sid in (ids["sibling-b"], ids["sibling-c"]):
            self.assertEqual(
                self.task_row(sid)["status"], "ready",
                f"sibling {sid} must never have been launched",
            )


if __name__ == "__main__":
    unittest.main()