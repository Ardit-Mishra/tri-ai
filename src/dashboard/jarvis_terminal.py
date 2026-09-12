"""Rich terminal dashboard over authoritative Tri-AI evidence.

This module is intentionally a reader. It opens the board through SQLite's
read-only URI mode, tails the append-only ledger, and reads the supervisor's
retained state file. It neither imports the board API nor has network,
credential, task-control, or process-spawn capability.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


DEFAULT_RUNTIME_ROOT = Path.home() / ".tri-ai"
PidAlive = Callable[[int], bool]


class DashboardSourceError(RuntimeError):
    """An authoritative source cannot be read as required for a snapshot."""


@dataclass(frozen=True)
class TaskView:
    task_id: str
    title: str
    status: str
    run_id: Optional[int]


@dataclass(frozen=True)
class TaskEdge:
    parent_id: str
    child_id: str


@dataclass(frozen=True)
class LedgerEvent:
    timestamp: object
    task_id: str
    outcome: str
    verify_exit: object


@dataclass(frozen=True)
class DaemonHealth:
    status: str
    processes: tuple[tuple[str, bool], ...]
    diagnostic: Optional[str]


@dataclass(frozen=True)
class DashboardSnapshot:
    tasks: tuple[TaskView, ...]
    edges: tuple[TaskEdge, ...]
    ledger_events: tuple[LedgerEvent, ...]
    ledger_errors: tuple[str, ...]
    activated_rule_count: int
    daemons: DaemonHealth


def _readonly_board(board_path: Path | str) -> sqlite3.Connection:
    path = Path(board_path).resolve()
    if not path.is_file():
        raise DashboardSourceError(f"board database does not exist: {path}")
    try:
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise DashboardSourceError(f"board database is unreadable: {path}") from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
    ).fetchone() is not None


def _read_board(board_path: Path | str) -> tuple[tuple[TaskView, ...], tuple[TaskEdge, ...], int]:
    conn = _readonly_board(board_path)
    try:
        try:
            task_rows = conn.execute(
                "SELECT id, title, status, current_run_id FROM tasks "
                "ORDER BY priority DESC, created_at ASC"
            ).fetchall()
            edge_rows = conn.execute(
                "SELECT parent_id, child_id FROM task_links ORDER BY parent_id, child_id"
            ).fetchall()
        except sqlite3.Error as exc:
            raise DashboardSourceError("board schema cannot supply dashboard evidence") from exc
        rule_count = 0
        if _table_exists(conn, "triai_activated_procedural_rules"):
            rule_count = int(conn.execute(
                "SELECT COUNT(*) FROM triai_activated_procedural_rules"
            ).fetchone()[0])
        return (
            tuple(TaskView(row["id"], row["title"], row["status"], row["current_run_id"]) for row in task_rows),
            tuple(TaskEdge(row["parent_id"], row["child_id"]) for row in edge_rows),
            rule_count,
        )
    finally:
        conn.close()


def _read_ledger(path: Path | str, *, limit: int) -> tuple[tuple[LedgerEvent, ...], tuple[str, ...]]:
    if limit < 1:
        raise ValueError("ledger_limit must be positive")
    target = Path(path)
    if not target.is_file():
        return (), (f"ledger does not exist: {target}",)
    events: list[LedgerEvent] = []
    errors: list[str] = []
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return (), (f"ledger is unreadable: {target} ({type(exc).__name__})",)
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"ledger line {line_number} is not valid JSON")
            continue
        if not isinstance(raw, dict):
            errors.append(f"ledger line {line_number} is not an object")
            continue
        task_id = raw.get("task_id")
        outcome = raw.get("outcome")
        if not isinstance(task_id, str) or not isinstance(outcome, str):
            errors.append(f"ledger line {line_number} lacks task_id or outcome")
            continue
        events.append(LedgerEvent(raw.get("ts"), task_id, outcome, raw.get("verify_exit")))
    return tuple(reversed(events[-limit:])), tuple(errors)


def _pid_alive(pid: int) -> bool:
    """Read one process-liveness fact without signalling or controlling it."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def _read_daemon_health(
    state_path: Path | str, *, pid_alive: PidAlive,
) -> DaemonHealth:
    path = Path(state_path)
    if not path.is_file():
        return DaemonHealth("unknown", (), f"supervisor state does not exist: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return DaemonHealth("unknown", (), f"supervisor state is unreadable: {type(exc).__name__}")
    if not isinstance(raw, dict) or not isinstance(raw.get("status"), str):
        return DaemonHealth("unknown", (), "supervisor state has no status")
    processes: list[tuple[str, bool]] = []
    for label, field in (("supervisor", "supervisor_pid"), ("worker", "worker_pid"), ("telegram", "telegram_pid")):
        pid = raw.get(field)
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
            processes.append((label, pid_alive(pid)))
        else:
            processes.append((label, False))
    return DaemonHealth(raw["status"], tuple(processes), None)


def read_snapshot(
    *,
    board_path: Path | str,
    ledger_path: Path | str,
    daemon_state_path: Path | str,
    ledger_limit: int = 12,
    pid_alive: PidAlive = _pid_alive,
) -> DashboardSnapshot:
    """Read one immutable dashboard snapshot from the authoritative sources."""
    tasks, edges, activated_rule_count = _read_board(board_path)
    events, ledger_errors = _read_ledger(ledger_path, limit=ledger_limit)
    return DashboardSnapshot(
        tasks=tasks,
        edges=edges,
        ledger_events=events,
        ledger_errors=ledger_errors,
        activated_rule_count=activated_rule_count,
        daemons=_read_daemon_health(daemon_state_path, pid_alive=pid_alive),
    )


def _status_counts(tasks: Sequence[TaskView]) -> str:
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.status] = counts.get(task.status, 0) + 1
    return ", ".join(f"{status}={count}" for status, count in sorted(counts.items())) or "none"


def _renderable(snapshot: DashboardSnapshot) -> Group:
    heading = Panel(
        Text("TRI-AI JARVIS | Read-only evidence dashboard", style="bold cyan"),
        subtitle=f"tasks={len(snapshot.tasks)} ({_status_counts(snapshot.tasks)}) | accepted rules={snapshot.activated_rule_count}",
    )
    tasks = Table(title="Board", expand=True)
    tasks.add_column("Task", style="cyan", no_wrap=True)
    tasks.add_column("Status")
    tasks.add_column("Run", justify="right")
    tasks.add_column("Title", overflow="fold")
    for task in snapshot.tasks:
        tasks.add_row(task.task_id, task.status, "-" if task.run_id is None else str(task.run_id), task.title)

    edges = Table(title="Dependencies", expand=True)
    edges.add_column("Parent -> child")
    if snapshot.edges:
        for edge in snapshot.edges:
            edges.add_row(f"{edge.parent_id} -> {edge.child_id}")
    else:
        edges.add_row("none")

    events = Table(title="Recent ledger events (newest first)", expand=True)
    events.add_column("Time")
    events.add_column("Task")
    events.add_column("Outcome")
    events.add_column("Verify exit")
    if snapshot.ledger_events:
        for event in snapshot.ledger_events:
            events.add_row(str(event.timestamp), event.task_id, event.outcome, str(event.verify_exit))
    else:
        events.add_row("-", "-", "no valid entries", "-")

    health = ", ".join(
        f"{label}: {'up' if alive else 'down'}" for label, alive in snapshot.daemons.processes
    ) or "no recorded processes"
    health_panel = Panel(
        f"state: {snapshot.daemons.status}\n{health}"
        + (f"\n{snapshot.daemons.diagnostic}" if snapshot.daemons.diagnostic else ""),
        title="Daemon supervisor",
    )
    diagnostics = Panel(
        "\n".join(snapshot.ledger_errors) if snapshot.ledger_errors else "none",
        title="Ledger diagnostics",
    )
    return Group(heading, tasks, edges, events, health_panel, diagnostics)


def render_snapshot(snapshot: DashboardSnapshot, *, console: Console) -> None:
    """Render a snapshot through an injected Rich console for testability."""
    console.print(_renderable(snapshot))


def _arguments(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render the read-only Tri-AI JARVIS terminal dashboard.")
    parser.add_argument("--board", type=Path, default=DEFAULT_RUNTIME_ROOT / "board.db")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_RUNTIME_ROOT / "ledger.jsonl")
    parser.add_argument("--daemon-state", type=Path, default=DEFAULT_RUNTIME_ROOT / "logs" / "daemons.json")
    parser.add_argument("--ledger-limit", type=int, default=12)
    parser.add_argument("--watch", action="store_true", help="Refresh without modifying any source.")
    parser.add_argument("--refresh-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)
    if args.ledger_limit < 1:
        parser.error("--ledger-limit must be positive")
    if args.refresh_seconds <= 0:
        parser.error("--refresh-seconds must be positive")
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _arguments(argv)
    console = Console()

    def snapshot() -> DashboardSnapshot:
        return read_snapshot(
            board_path=args.board,
            ledger_path=args.ledger,
            daemon_state_path=args.daemon_state,
            ledger_limit=args.ledger_limit,
        )

    try:
        if not args.watch:
            render_snapshot(snapshot(), console=console)
            return 0
        with Live(console=console, refresh_per_second=4, transient=True) as live:
            while True:
                live.update(_renderable(snapshot()))
                time.sleep(args.refresh_seconds)
    except KeyboardInterrupt:
        return 0
    except (DashboardSourceError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"jarvis dashboard stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
