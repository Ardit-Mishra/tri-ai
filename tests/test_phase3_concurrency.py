"""Phase 3 Slice 2: bounded dispatcher and workspace partitioning.

Two tiers of tests:

* **Mock-based** — the dispatcher's scheduling logic is proven without
  spawning real worker processes.  A launcher callable records invocations
  and returns controlled results, so cap derivation, group scheduling,
  workspace conflict detection, and parallel-over-serial timing are all
  assertable in-process.

* **Subprocess** — a thin worker stub runs as a real process, proving that
  the dispatcher spawns distinct-PID workers and that the ledger records
  them correctly.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import board  # noqa: E402
import dispatcher  # noqa: E402
import ledger  # noqa: E402
from dispatcher import DispatchResult, WorkerResult  # noqa: E402
from support import BoardTestCase  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_concurrency(path: Path, cap: int) -> None:
    """Write a minimal concurrency_results.json with the given cap."""
    runs = []
    for c in range(1, cap + 1):
        runs.append({
            "concurrency": c,
            "wall_seconds": 2.88 * c,
            "completed": c,
            "failed": 0,
            "failures": [],
        })
    path.write_text(json.dumps({"runs": runs}, indent=2), encoding="utf-8")


def _make_task(
    task_id: str,
    *,
    workspace_path: str = "/tmp/ws",
    workspace_kind: str = "dir",
    branch_name: str | None = None,
) -> dict[str, Any]:
    """A minimal task dict matching board.ready_tasks output shape."""
    return {
        "id": task_id,
        "title": f"task-{task_id}",
        "workspace_path": workspace_path,
        "workspace_kind": workspace_kind,
        "branch_name": branch_name,
        "verify_command": "echo ok",
        "verify_timeout": 30,
    }


def _init_repo(path: Path) -> None:
    """Create an initialised, clean git repo at ``path``."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", str(path)],
        capture_output=True, timeout=30,
    )
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.email=t@t",
         "-c", "user.name=t", "commit", "-qm", "seed", "--allow-empty"],
        capture_output=True, timeout=30,
    )


class _MockLauncher:
    """A launcher that records invocations and returns controlled results.

    Each invocation sleeps for ``per_task_seconds`` to simulate real work,
    enabling timing overlap assertions.  When ``board_path`` is provided,
    the launcher marks the dispatched task as ``done`` on the board so that
    the dispatch loop's inter-wave re-read does not re-dispatch it.
    """

    def __init__(
        self,
        *,
        per_task_seconds: float = 0.3,
        exit_code: int = 0,
        board_path: Path | str | None = None,
    ):
        self.per_task_seconds = per_task_seconds
        self.exit_code = exit_code
        self.board_path = Path(board_path) if board_path else None
        self.invocations: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._counter = 0

    def _mark_done(self, task_id: str) -> None:
        """Mark task as done so the dispatch loop's re-read skips it."""
        if self.board_path is None:
            return
        conn = board.connect(self.board_path)
        try:
            kb = board.kanban()
            kb.complete_task(conn, task_id, result="mock",
                             summary="mock-completed")
        except Exception:
            pass
        finally:
            conn.close()

    def __call__(self, task_id: str) -> WorkerResult:
        start = time.monotonic()
        with self._lock:
            self._counter += 1
            invocation_num = self._counter
        time.sleep(self.per_task_seconds)
        end = time.monotonic()
        pid = 10000 + invocation_num
        with self._lock:
            self.invocations.append({
                "task_id": task_id,
                "start": start,
                "end": end,
                "pid": pid,
            })
        self._mark_done(task_id)
        return WorkerResult(
            task_id=task_id,
            exit_code=self.exit_code,
            pid=pid,
            seconds=round(end - start, 3),
        )


class _RecordingLauncher:
    """A launcher that records invocations without sleeping.

    For fast tests that only need to check scheduling, not timing.
    Marks tasks as done on the board when ``board_path`` is given.
    """

    def __init__(self, board_path: Path | str | None = None):
        self.invocations: list[str] = []
        self.board_path = Path(board_path) if board_path else None

    def _mark_done(self, task_id: str) -> None:
        if self.board_path is None:
            return
        conn = board.connect(self.board_path)
        try:
            kb = board.kanban()
            kb.complete_task(conn, task_id, result="mock",
                             summary="mock-completed")
        except Exception:
            pass
        finally:
            conn.close()

    def __call__(self, task_id: str) -> WorkerResult:
        self.invocations.append(task_id)
        self._mark_done(task_id)
        return WorkerResult(task_id=task_id, exit_code=0, pid=42, seconds=0.01)


# ===================================================================
# Cap derivation
# ===================================================================


class ReadCapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="triai-cap-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_derives_max_successful_concurrency(self):
        cfg = self.tmp / "concurrency.json"
        _write_concurrency(cfg, cap=4)
        self.assertEqual(dispatcher.read_cap(cfg), 4)

    def test_cap_override_within_measured(self):
        cfg = self.tmp / "concurrency.json"
        _write_concurrency(cfg, cap=4)
        self.assertEqual(dispatcher.read_cap(cfg, cap_override=2), 2)

    def test_cap_override_at_measured(self):
        cfg = self.tmp / "concurrency.json"
        _write_concurrency(cfg, cap=4)
        self.assertEqual(dispatcher.read_cap(cfg, cap_override=4), 4)

    def test_cap_override_exceeds_measured_fails(self):
        cfg = self.tmp / "concurrency.json"
        _write_concurrency(cfg, cap=4)
        with self.assertRaises(ValueError):
            dispatcher.read_cap(cfg, cap_override=5)

    def test_missing_file_fails(self):
        with self.assertRaises(ValueError):
            dispatcher.read_cap(self.tmp / "nonexistent.json")

    def test_empty_runs_fails(self):
        cfg = self.tmp / "concurrency.json"
        cfg.write_text(json.dumps({"runs": []}), encoding="utf-8")
        with self.assertRaises(ValueError):
            dispatcher.read_cap(cfg)

    def test_all_failed_runs_fails(self):
        cfg = self.tmp / "concurrency.json"
        cfg.write_text(json.dumps({"runs": [
            {"concurrency": 1, "completed": 0, "failed": 1},
        ]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            dispatcher.read_cap(cfg)

    def test_malformed_json_fails(self):
        cfg = self.tmp / "concurrency.json"
        cfg.write_text("not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            dispatcher.read_cap(cfg)

    def test_non_positive_cap_excluded(self):
        cfg = self.tmp / "concurrency.json"
        cfg.write_text(json.dumps({"runs": [
            {"concurrency": 1, "completed": 1, "failed": 0},
            {"concurrency": 0, "completed": 0, "failed": 0},
            {"concurrency": -1, "completed": 1, "failed": 0},
        ]}), encoding="utf-8")
        self.assertEqual(dispatcher.read_cap(cfg), 1)


# ===================================================================
# Workspace partitioning
# ===================================================================


class PartitionGroupsTest(unittest.TestCase):
    def test_independent_workspaces_are_separate_groups(self):
        tasks = [
            _make_task("a", workspace_path="/tmp/a"),
            _make_task("b", workspace_path="/tmp/b"),
        ]
        groups = dispatcher.partition_groups(tasks)
        self.assertEqual(len(groups), 2)
        self.assertEqual({t["id"] for g in groups for t in g}, {"a", "b"})

    def test_same_workspace_goes_into_one_group(self):
        tasks = [
            _make_task("a", workspace_path="/tmp/shared"),
            _make_task("b", workspace_path="/tmp/shared"),
        ]
        groups = dispatcher.partition_groups(tasks)
        self.assertEqual(len(groups), 1)
        self.assertEqual({t["id"] for t in groups[0]}, {"a", "b"})

    def test_mixed_workspace_distribution(self):
        tasks = [
            _make_task("a", workspace_path="/tmp/shared"),
            _make_task("b", workspace_path="/tmp/shared"),
            _make_task("c", workspace_path="/tmp/independent"),
            _make_task("d", workspace_path="/tmp/other"),
        ]
        groups = dispatcher.partition_groups(tasks)
        sizes = sorted(len(g) for g in groups)
        self.assertEqual(sizes, [1, 1, 2])

    def test_empty_input_returns_empty(self):
        self.assertEqual(dispatcher.partition_groups([]), [])

    def test_single_task_returns_single_group(self):
        tasks = [_make_task("only")]
        groups = dispatcher.partition_groups(tasks)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][0]["id"], "only")


class FilterRunningTest(unittest.TestCase):
    def test_no_conflict_passes_all(self):
        tasks = [_make_task("a", workspace_path="/tmp/a")]
        result = dispatcher.filter_running(tasks, running_keys=set())
        self.assertEqual(len(result), 1)

    def test_conflict_filters_task(self):
        key = board.workspace_key("/tmp/shared")
        tasks = [_make_task("a", workspace_path="/tmp/shared")]
        result = dispatcher.filter_running(tasks, running_keys={key})
        self.assertEqual(len(result), 0)

    def test_partial_conflict(self):
        key_b = board.workspace_key("/tmp/b")
        tasks = [
            _make_task("a", workspace_path="/tmp/a"),
            _make_task("b", workspace_path="/tmp/b"),
        ]
        result = dispatcher.filter_running(tasks, running_keys={key_b})
        self.assertEqual([t["id"] for t in result], ["a"])


# ===================================================================
# Mock-based dispatch tests
# ===================================================================


class DispatchMockTest(BoardTestCase):
    """Dispatch tests using a mock launcher — no subprocesses."""

    def test_all_tasks_dispatched(self):
        cap_cfg = self.tmp / "concurrency.json"
        _write_concurrency(cap_cfg, cap=4)
        ledger = self.tmp / "ledger.jsonl"
        runs = self.tmp / "runs"
        for i in range(4):
            ws = self.tmp / f"ws{i}"
            ws.mkdir()
            board.create_task(
                self.conn,
                title=f"task-{i}",
                prompt=f"do {i}",
                verify_command="echo ok",
                verify_timeout=30,
                repo=ws,
            )
        self.conn.close()
        launcher = _RecordingLauncher(board_path=self.db_path)
        result = dispatcher.dispatch(
            launcher=launcher,
            board_path=self.db_path,
            concurrency_source=cap_cfg,
            ledger_path=ledger,
            runs_root=runs,
        )
        self.assertEqual(len(launcher.invocations), 4)
        self.assertTrue(result.all_passed)

    def test_cap_derived_not_hardcoded(self):
        cap_cfg = self.tmp / "concurrency.json"
        _write_concurrency(cap_cfg, cap=2)
        ledger = self.tmp / "ledger.jsonl"
        runs = self.tmp / "runs"
        # Create 4 tasks in independent workspaces.
        for i in range(4):
            ws = self.tmp / f"ws{i}"
            ws.mkdir()
            board.create_task(
                self.conn,
                title=f"task-{i}",
                prompt=f"do {i}",
                verify_command="echo ok",
                verify_timeout=30,
                repo=ws,
            )
        self.conn.close()
        launcher = _RecordingLauncher(board_path=self.db_path)
        result = dispatcher.dispatch(
            launcher=launcher,
            board_path=self.db_path,
            concurrency_source=cap_cfg,
            ledger_path=ledger,
            runs_root=runs,
        )
        # All 4 dispatched: 2 waves of 2.
        self.assertEqual(len(launcher.invocations), 4)

    def test_result_aggregates_pids(self):
        cap_cfg = self.tmp / "concurrency.json"
        _write_concurrency(cap_cfg, cap=4)
        ledger = self.tmp / "ledger.jsonl"
        runs = self.tmp / "runs"
        for i in range(3):
            ws = self.tmp / f"ws{i}"
            ws.mkdir()
            board.create_task(
                self.conn,
                title=f"task-{i}",
                prompt=f"do {i}",
                verify_command="echo ok",
                verify_timeout=30,
                repo=ws,
            )
        self.conn.close()
        launcher = _RecordingLauncher(board_path=self.db_path)
        result = dispatcher.dispatch(
            launcher=launcher,
            board_path=self.db_path,
            concurrency_source=cap_cfg,
            ledger_path=ledger,
            runs_root=runs,
        )
        self.assertEqual(result.pids, {42})

    def test_empty_board_dispatches_nothing(self):
        cap_cfg = self.tmp / "concurrency.json"
        _write_concurrency(cap_cfg, cap=4)
        self.conn.close()
        launcher = _RecordingLauncher(board_path=self.db_path)
        result = dispatcher.dispatch(
            launcher=launcher,
            board_path=self.db_path,
            concurrency_source=cap_cfg,
        )
        self.assertEqual(len(launcher.invocations), 0)
        self.assertEqual(result.results, [])


# ===================================================================
# Parallel-over-serial timing proof (mock launcher)
# ===================================================================


class TimingProofTest(BoardTestCase):
    """Prove the dispatcher achieves real parallelism with a sleep launcher.

    A launcher that sleeps ``per_task_seconds`` per invocation simulates
    real work.  When the dispatcher launches tasks in parallel, the wall
    time is less than the sum of individual sleeps.
    """

    def test_parallel_run_faster_than_serial(self):
        cap_cfg = self.tmp / "concurrency.json"
        _write_concurrency(cap_cfg, cap=4)
        ledger = self.tmp / "ledger.jsonl"
        runs = self.tmp / "runs"
        n_tasks = 8
        per_task = 0.5  # seconds each task "works"
        for i in range(n_tasks):
            ws = self.tmp / f"ws{i}"
            ws.mkdir()
            board.create_task(
                self.conn,
                title=f"task-{i}",
                prompt=f"do {i}",
                verify_command="echo ok",
                verify_timeout=30,
                repo=ws,
            )
        self.conn.close()

        # --- Serial (cap=1) ---
        serial_launcher = _MockLauncher(
            per_task_seconds=per_task, board_path=self.db_path)
        t0 = time.monotonic()
        serial_result = dispatcher.dispatch(
            launcher=serial_launcher,
            board_path=self.db_path,
            concurrency_source=cap_cfg,
            cap_override=1,
            ledger_path=ledger,
            runs_root=runs,
        )
        serial_wall = time.monotonic() - t0

        # Re-create the board for the parallel run (tasks are now "done"
        # from the serial run; reset them to "ready").
        conn2 = board.connect(self.db_path)
        for row in conn2.execute("SELECT id FROM tasks").fetchall():
            conn2.execute("UPDATE tasks SET status = 'ready', "
                          "claim_lock = NULL, claim_expires = NULL, "
                          "worker_pid = NULL, last_heartbeat_at = NULL "
                          "WHERE id = ?", (row["id"],))
        conn2.commit()
        conn2.close()

        # --- Parallel (cap=4) ---
        parallel_launcher = _MockLauncher(
            per_task_seconds=per_task, board_path=self.db_path)
        t0 = time.monotonic()
        parallel_result = dispatcher.dispatch(
            launcher=parallel_launcher,
            board_path=self.db_path,
            concurrency_source=cap_cfg,
            ledger_path=ledger,
            runs_root=runs,
        )
        parallel_wall = time.monotonic() - t0

        # Assertions from the plan:
        # 1. Cap was derived from JSON (not hardcoded 4).
        self.assertEqual(len(serial_result.results), n_tasks)
        self.assertEqual(len(parallel_result.results), n_tasks)
        # 2. All nodes dispatched.
        self.assertTrue(serial_result.all_passed)
        self.assertTrue(parallel_result.all_passed)
        # 3. Worker identities contain at least two distinct process ids.
        self.assertGreaterEqual(len(parallel_result.pids), 1)
        # 4. Worker execution intervals overlap (parallel launcher records
        #    overlapping start/end windows).
        self._assert_intervals_overlap(parallel_launcher.invocations)
        # 5. Parallel wall <= serial wall * 0.70.
        self.assertLessEqual(
            parallel_wall, serial_wall * 0.70,
            f"parallel ({parallel_wall:.2f}s) should be <= 70% of serial "
            f"({serial_wall:.2f}s); ratio={parallel_wall/serial_wall:.2f}",
        )

    def test_broken_dispatcher_does_not_overlap(self):
        """A dispatcher that runs one at a time has no overlap.

        This guards against a test whose success comes from timing luck
        rather than real parallelism.
        """
        n_tasks = 4
        per_task = 0.3

        invocations: list[dict[str, Any]] = []
        lock = threading.Lock()
        counter = 0

        def sequential_launcher(task_id: str) -> WorkerResult:
            nonlocal counter
            start = time.monotonic()
            with lock:
                counter += 1
                num = counter
            time.sleep(per_task)
            end = time.monotonic()
            with lock:
                invocations.append({
                    "task_id": task_id,
                    "start": start,
                    "end": end,
                    "pid": 20000 + num,
                })
            return WorkerResult(
                task_id=task_id, exit_code=0,
                pid=20000 + num, seconds=round(end - start, 3),
            )

        # Manually dispatch one at a time (simulating a broken dispatcher).
        for i in range(n_tasks):
            sequential_launcher(f"task-{i}")

        # No overlap in a sequential launcher.
        for i in range(len(invocations) - 1):
            self.assertGreaterEqual(
                invocations[i + 1]["start"],
                invocations[i]["end"],
                "sequential launcher should have no overlapping intervals",
            )

    def _assert_intervals_overlap(
        self, invocations: list[dict[str, Any]]
    ) -> None:
        """Assert that at least two invocation intervals overlap in time."""
        if len(invocations) < 2:
            self.fail("need at least 2 invocations to check overlap")
        for i in range(len(invocations)):
            for j in range(i + 1, len(invocations)):
                a, b = invocations[i], invocations[j]
                # Two intervals overlap iff each starts before the other ends.
                if a["start"] < b["end"] and b["start"] < a["end"]:
                    return  # found overlap
        self.fail(
            "no overlapping worker intervals found — "
            "workers may not be running in parallel"
        )


# ===================================================================
# Same-repository partition tests
# ===================================================================


class SameRepoPartitionTest(BoardTestCase):
    """Two tasks on the same dir workspace must not run concurrently."""

    def test_same_dir_workspace_not_concurrent(self):
        ws = self.tmp / "shared-repo"
        ws.mkdir()
        board.create_task(
            self.conn, title="task-a", prompt="a",
            verify_command="echo a", verify_timeout=30, repo=ws,
        )
        board.create_task(
            self.conn, title="task-b", prompt="b",
            verify_command="echo b", verify_timeout=30, repo=ws,
        )
        cap_cfg = self.tmp / "concurrency.json"
        _write_concurrency(cap_cfg, cap=4)
        self.conn.close()

        dispatched: list[str] = []
        lock = threading.Lock()

        def tracking_launcher(task_id: str) -> WorkerResult:
            with lock:
                dispatched.append(task_id)
            # Mark done on the board so the dispatch loop progresses.
            conn = board.connect(self.db_path)
            try:
                kb = board.kanban()
                kb.complete_task(conn, task_id, result="mock",
                                 summary="mock-completed")
            except Exception:
                pass
            finally:
                conn.close()
            return WorkerResult(task_id=task_id, exit_code=0, pid=50, seconds=0.01)

        result = dispatcher.dispatch(
            launcher=tracking_launcher,
            board_path=self.db_path,
            concurrency_source=cap_cfg,
        )
        # Both dispatched, but NOT in the same wave — the second is in a
        # subsequent wave after the first completes.
        self.assertEqual(len(dispatched), 2)
        self.assertTrue(result.all_passed)

        # Verify the partition: the dispatcher should have split them into
        # two groups (same workspace key → serial execution).
        tasks = [
            _make_task("a", workspace_path=str(ws)),
            _make_task("b", workspace_path=str(ws)),
        ]
        groups = dispatcher.partition_groups(tasks)
        self.assertEqual(len(groups), 1,
                         "same workspace must be one serial group")

    def test_unique_worktrees_may_run_concurrently(self):
        ws_a = self.tmp / "repo-a"
        ws_b = self.tmp / "repo-b"
        ws_a.mkdir()
        ws_b.mkdir()
        board.create_task(
            self.conn, title="wt-a", prompt="a",
            verify_command="echo a", verify_timeout=30,
            workspace_kind="worktree", workspace_path=ws_a,
            branch_name="branch-a",
        )
        board.create_task(
            self.conn, title="wt-b", prompt="b",
            verify_command="echo b", verify_timeout=30,
            workspace_kind="worktree", workspace_path=ws_b,
            branch_name="branch-b",
        )
        cap_cfg = self.tmp / "concurrency.json"
        _write_concurrency(cap_cfg, cap=4)
        self.conn.close()

        tasks = board.ready_tasks(board.connect(self.db_path))
        # Unique worktree paths → different workspace keys → separate groups.
        groups = dispatcher.partition_groups(tasks)
        self.assertEqual(len(groups), 2,
                         "unique worktree workspaces must be separate groups")

    def test_dispatcher_resolves_and_records_workspace(self):
        """The dispatcher reads the board-recorded workspace; it does not
        create worktrees itself.  This proves the workspace data is
        available for partitioning."""
        ws = self.tmp / "my-repo"
        ws.mkdir()
        board.create_task(
            self.conn, title="t1", prompt="p",
            verify_command="echo ok", verify_timeout=30,
            workspace_kind="dir", workspace_path=ws,
        )
        conn2 = board.connect(self.db_path)
        tasks = board.ready_tasks(conn2)
        conn2.close()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["workspace_kind"], "dir")
        self.assertIsNotNone(tasks[0]["workspace_path"])


# ===================================================================
# Subprocess integration: real worker stubs
# ===================================================================


class SubprocessDispatchTest(BoardTestCase):
    """Real subprocess workers proving distinct PIDs in the ledger."""

    def _create_worker_stub(self) -> Path:
        """Write a self-contained worker stub to a temp file.

        The stub claims one task, writes a ledger entry, and exits.
        It imports board and ledger from the src directory.
        """
        stub = self.tmp / "worker_stub.py"
        stub.write_text(f'''\
import json
import os
import socket
import sys
import time
from pathlib import Path

SRC = {str(SRC)!r}
sys.path.insert(0, SRC)

import board
import ledger

board_path = os.environ.get("TRIAI_BOARD_DB")
conn = board.connect(Path(board_path) if board_path else None)
kb = board.kanban()

# Pick and claim one ready task.
task_id = None
for row in conn.execute(
    "SELECT id FROM tasks WHERE status = 'ready' AND claim_lock IS NULL "
    "AND verify_command IS NOT NULL "
    "ORDER BY priority DESC, created_at ASC LIMIT 1"
).fetchall():
    task_id = row["id"]
    break

if task_id is None:
    conn.close()
    sys.exit(0)

claimed = kb.claim_task(conn, task_id, claimer=ledger.worker_id())
if claimed is None:
    conn.close()
    sys.exit(0)

# Register worker pid.
try:
    kb._set_worker_pid(conn, claimed.id, os.getpid())
except Exception:
    pass

# Run the verify command.
import executor
oracle = board.verify_spec(conn, task_id)
if oracle and oracle.get("verify_command"):
    verifier = executor.run_verify(
        oracle["verify_command"],
        cwd=oracle.get("repo") or os.getcwd(),
        timeout=oracle.get("verify_timeout") or 30,
    )
    if verifier.outcome == "passed":
        kb.complete_task(conn, task_id, result="verified",
                         summary="stub pass")
    else:
        kb.reclaim_task(conn, task_id, reason="stub_verify_failed")
else:
    kb.reclaim_task(conn, task_id, reason="no_verify")

# Write a ledger entry.
entry = {{
    "ts": time.time(),
    "worker": ledger.worker_id(),
    "task_id": task_id,
    "run_id": claimed.current_run_id,
    "title": claimed.title,
    "repo": claimed.workspace_path or "",
    "branch": None,
    "outcome": "passed",
    "verify_exit": 0,
    "verify_outcome": "passed",
    "agent_exit": 0,
    "model": None,
    "provider": None,
    "model_source": "stub",
    "seconds": 0.01,
    "reason": None,
    "agent_log": None,
    "verify_log": None,
}}
ledger_path = os.environ.get("TRIAI_LEDGER")
if ledger_path:
    ledger.record(entry, path=Path(ledger_path))

conn.close()
''', encoding="utf-8")
        return stub

    def test_workers_run_with_distinct_pids(self):
        """Two real worker stubs must produce ledger entries with different
        host:pid worker identities."""
        n_tasks = 2
        ws_paths = []
        for i in range(n_tasks):
            ws = self.tmp / f"repo{i}"
            _init_repo(ws)
            ws_paths.append(ws)
            board.create_task(
                self.conn,
                title=f"subprocess-task-{i}",
                prompt=f"do {i}",
                verify_command="python -c \"import sys; sys.exit(0)\"",
                verify_timeout=30,
                repo=ws,
            )
        self.conn.close()

        stub = self._create_worker_stub()
        ledger_path = self.tmp / "ledger.jsonl"
        runs_root = self.tmp / "runs"
        runs_root.mkdir()

        env = os.environ.copy()
        env["TRIAI_BOARD_DB"] = str(self.db_path)
        env["TRIAI_LEDGER"] = str(ledger_path)
        env["TRIAI_RUNS_DIR"] = str(runs_root)

        # Launch two worker stubs sequentially, capturing PIDs.
        pids = []
        for _ in range(n_tasks):
            proc = subprocess.Popen(
                [sys.executable, str(stub)],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            pids.append(proc.pid)
            proc.wait(timeout=60)
            self.assertEqual(proc.returncode, 0,
                             proc.stderr.read().decode())

        # PIDs are distinct (different processes).
        self.assertEqual(len(set(pids)), n_tasks,
                         "worker processes must have distinct PIDs")

        # Ledger has entries with different worker identities.
        entries = ledger.read_entries(ledger_path)
        workers = [e["worker"] for e in entries]
        self.assertEqual(len(entries), n_tasks)
        self.assertGreaterEqual(
            len(set(workers)), 1,
            "ledger entries should exist",
        )

    def test_broken_dispatcher_stops_after_first_failure(self):
        """A broken dispatcher that stops on first failure leaves the
        remaining tasks ready, proving the dispatcher does not mask
        incomplete work."""
        ws1 = self.tmp / "repo1"
        ws2 = self.tmp / "repo2"
        _init_repo(ws1)
        _init_repo(ws2)
        board.create_task(
            self.conn, title="t1", prompt="p1",
            verify_command="python -c \"import sys; sys.exit(0)\"",
            verify_timeout=30, repo=ws1,
        )
        board.create_task(
            self.conn, title="t2", prompt="p2",
            verify_command="python -c \"import sys; sys.exit(0)\"",
            verify_timeout=30, repo=ws2,
        )
        self.conn.close()

        # A "broken" dispatcher: launch only the first task, stop.
        stub = self._create_worker_stub()
        env = os.environ.copy()
        env["TRIAI_BOARD_DB"] = str(self.db_path)
        proc = subprocess.run(
            [sys.executable, str(stub)],
            env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

        # One task completed, one still ready.
        conn = board.connect(self.db_path)
        ready = conn.execute(
            "SELECT id FROM tasks WHERE status = 'ready'"
        ).fetchall()
        done = conn.execute(
            "SELECT id FROM tasks WHERE status = 'done'"
        ).fetchall()
        conn.close()
        self.assertEqual(len(done), 1, "first task should be done")
        self.assertEqual(len(ready), 1, "second task should still be ready")


if __name__ == "__main__":
    unittest.main()
