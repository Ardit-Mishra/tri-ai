"""Bounded concurrent dispatcher for Phase 3 verified execution.

Reads the measured worker cap from ``concurrency_results.json``, partitions
ready tasks by workspace to prevent same-repo races, and launches workers in
waves up to the cap.  The dispatcher does not retry, quarantine, or record
ledger entries — the Phase 2 worker owns all of that.  The dispatcher only
waits for its worker processes to exit and reports their exit status.

Non-negotiables:

- The cap is **derived** from ``concurrency_results.json``, never hardcoded.
- Workers are **processes** (not threads) so each ledger entry has a distinct
  ``host:pid`` identity.
- Same-repository ``dir`` workspaces are **serialised** — only one may run at
  a time.  ``worktree`` workspaces with distinct branches may run concurrently.
- The dispatcher launches only ``python src/worker.py --once`` — it never
  calls the Hermes dispatcher, which bypasses Tri-AI's verify-gated path.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import board

# ---------------------------------------------------------------------------
# Cap derivation from measured concurrency
# ---------------------------------------------------------------------------

DEFAULT_CAP_SOURCE = (
    Path(__file__).resolve().parents[1]
    / ".planning" / "research" / "concurrency_results.json"
)


def read_cap(
    source: Path | str | None = None,
    *,
    cap_override: int | None = None,
) -> int:
    """Derive the worker cap from measured concurrency evidence.

    ``cap_override`` bypasses the file for explicit operator control (e.g. a
    dry-run at cap 1).  It is still checked against the measured ceiling.

    Raises ``ValueError`` when the file is malformed, contains no successful
    runs, or the override exceeds the measured cap.
    """
    path = Path(source) if source is not None else DEFAULT_CAP_SOURCE
    if not path.exists():
        raise ValueError(f"concurrency config not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"cannot read concurrency config {path}: {exc}") from exc

    runs = data.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError(f"concurrency config has no runs: {path}")

    positive_caps = [
        r["concurrency"]
        for r in runs
        if isinstance(r, dict)
        and r.get("completed", 0) > 0
        and r.get("failed", 0) == 0
        and isinstance(r.get("concurrency"), int)
        and r["concurrency"] > 0
    ]
    if not positive_caps:
        raise ValueError(
            "concurrency config has no successful positive-cap run — "
            "cannot derive a safe cap"
        )

    measured = max(positive_caps)

    if cap_override is not None:
        if not isinstance(cap_override, int) or cap_override <= 0:
            raise ValueError(f"cap_override must be a positive integer, got {cap_override}")
        if cap_override > measured:
            raise ValueError(
                f"requested cap {cap_override} exceeds measured cap {measured}"
            )
        return cap_override

    return measured


# ---------------------------------------------------------------------------
# Workspace partitioning
# ---------------------------------------------------------------------------


def partition_groups(
    tasks: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Partition ready tasks into serial groups by ``workspace_key``.

    Tasks sharing the same workspace key (same directory or worktree anchor +
    branch) must not run concurrently — they go into one group that the
    dispatcher runs serially.  Independent workspaces form separate groups
    that may run in parallel.

    The partition is purely mechanical: first-seen key starts a new group,
    subsequent tasks with the same key join it.
    """
    groups: list[list[dict[str, Any]]] = []
    key_index: dict[str, int] = {}
    for task in tasks:
        key = task.get("workspace_key") or board.workspace_key(
            task.get("workspace_path") or ""
        )
        if key in key_index:
            groups[key_index[key]].append(task)
        else:
            key_index[key] = len(groups)
            groups.append([task])
    return groups


# ---------------------------------------------------------------------------
# Worker launcher
# ---------------------------------------------------------------------------


def _worker_argv(
    task_id: str,
    *,
    board_path: Path | str,
    ledger_path: Path | str | None = None,
    runs_root: Path | str | None = None,
) -> list[str]:
    """The exact ``python src/worker.py`` argv for one ``--once`` worker.

    The dispatcher launches *only* this form — it never invokes the Hermes
    dispatcher, which bypasses Tri-AI's verify-gated execution and ledger.
    """
    src = str(Path(__file__).resolve().parent)
    argv = [sys.executable, os.path.join(src, "worker.py"), "--once",
            "--board", str(board_path)]
    if ledger_path is not None:
        argv += ["--ledger", str(ledger_path)]
    if runs_root is not None:
        argv += ["--runs-dir", str(runs_root)]
    return argv


@dataclass
class WorkerResult:
    """Outcome of one worker invocation, reported by the launcher."""

    task_id: str
    exit_code: int
    pid: int | None = None
    seconds: float = 0.0
    error: str | None = None


# A Launcher is any callable that takes a task_id and returns a WorkerResult.
# The dispatcher owns scheduling; the launcher owns process creation.
Launcher = Callable[[str], WorkerResult]


def dispatch_one(
    task_id: str,
    *,
    launcher: Launcher,
    board_path: Path | str,
    ledger_path: Path | str | None = None,
    runs_root: Path | str | None = None,
) -> WorkerResult:
    """Dispatch exactly one task through the launcher.

    The launcher owns process creation and its environment. This function only
    invokes it and verifies that the returned result belongs to the requested
    task; a mismatched result would make the dispatcher report false evidence.
    """
    del board_path, ledger_path, runs_root
    result = launcher(task_id)
    if result.task_id != task_id:
        raise ValueError(
            f"launcher returned result for {result.task_id!r}, expected {task_id!r}"
        )
    return result


# ---------------------------------------------------------------------------
# The dispatch loop
# ---------------------------------------------------------------------------


@dataclass
class DispatchResult:
    """Aggregate outcome of one dispatch() invocation."""

    results: list[WorkerResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(r.exit_code == 0 for r in self.results)

    @property
    def pids(self) -> set[int]:
        return {r.pid for r in self.results if r.pid is not None}


def dispatch(
    *,
    launcher: Launcher,
    board_path: Path | str,
    cap_override: int | None = None,
    concurrency_source: Path | str | None = None,
    ledger_path: Path | str | None = None,
    runs_root: Path | str | None = None,
    max_waves: int | None = None,
) -> DispatchResult:
    """Dispatch all ready tasks through a bounded process pool.

    1. Read the worker cap from measured concurrency evidence.
    2. Read every ready task from the board.
    3. Partition by workspace key and filter out workspace conflicts.
    4. Schedule waves up to the cap: each wave takes one task from each
       serial group, dispatches them, and collects results before the next.
    5. Re-read ready tasks between waves — newly-promoted children join the
       pool, and reclaimed (failed-then-retried) tasks reappear naturally
       because ``ready_tasks()`` reflects live board state.

    The dispatcher does **not** retry, quarantine, or write ledger entries.
    The Phase 2 worker owns all of that.  The dispatcher only waits for its
    worker processes to exit and reports their exit status.

    ``max_waves`` caps the number of waves for testing.  ``None`` (default)
    means no limit — run until the board has no ready tasks.
    """
    cap = read_cap(
        concurrency_source or DEFAULT_CAP_SOURCE,
        cap_override=cap_override,
    )
    bp = Path(board_path).resolve()
    conn = board.connect(bp)
    result = DispatchResult()

    try:
        wave_index = 0
        while max_waves is None or wave_index < max_waves:
            # Re-read ready tasks from the board each wave.  This naturally
            # picks up: (a) newly-promoted children whose parents completed,
            # and (b) tasks reclaimed to ready after a failed verify attempt.
            ready = board.ready_tasks(conn)
            groups = partition_groups(ready)
            running_keys: set[str] = set()

            wave_tasks: list[dict[str, Any]] = []
            for group in groups:
                if not group:
                    continue
                task = group[0]
                key = task.get("workspace_key") or board.workspace_key(
                    task.get("workspace_path") or ""
                )
                if key not in running_keys:
                    wave_tasks.append(task)
                    running_keys.add(key)

            if not wave_tasks:
                break

            # Respect the cap: take at most `cap` tasks per wave.
            batch = wave_tasks[:cap]
            wave_result = run_batch(
                batch, launcher=launcher, board_path=bp,
                ledger_path=ledger_path, runs_root=runs_root,
            )
            result.results.extend(wave_result)
            wave_index += 1

    finally:
        try:
            conn.close()
        except Exception:
            pass

    return result


def run_batch(
    tasks: list[dict[str, Any]],
    *,
    launcher: Launcher,
    board_path: Path | str,
    ledger_path: Path | str | None = None,
    runs_root: Path | str | None = None,
) -> list[WorkerResult]:
    """Dispatch a batch of tasks concurrently through the launcher.

    Each task is launched in its own thread so that workers within a wave
    genuinely overlap in wall-clock time.  The launcher is responsible for
    spawning the actual worker process (or sleeping, for mock launchers).
    Results are collected in submission order.
    """
    import threading as _threading

    if len(tasks) <= 1:
        # Trivial case: no concurrency overhead.
        return [
            dispatch_one(
                task["id"],
                launcher=launcher,
                board_path=board_path,
                ledger_path=ledger_path,
                runs_root=runs_root,
            )
            for task in tasks
        ]

    results: list[WorkerResult | None] = [None] * len(tasks)

    def _run(idx: int, task_id: str) -> None:
        results[idx] = dispatch_one(
            task_id,
            launcher=launcher,
            board_path=board_path,
            ledger_path=ledger_path,
            runs_root=runs_root,
        )

    threads = []
    for idx, task in enumerate(tasks):
        t = _threading.Thread(target=_run, args=(idx, task["id"]))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    return [r for r in results if r is not None]
