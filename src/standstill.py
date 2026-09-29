"""Detect work that cannot move, and work that has stopped moving.

Draining a real build on the desktop exposed two silences.

**A cancelled parent strands its children forever.** `Write automated
tests` waited on two cancelled tasks, and `Integrate secure payment
processing` waited on that. A cancelled task never completes, so both sat
in `todo` permanently. The board looked busy and nothing would ever run.

**A stall is announced once and then never again.** Completion notices are
one per (task, run), deduped by a receipt in
`triai_completion_notifications`. A stalled task produces no new runs, so
it produces no new notices: the desktop sat still for three days after a
single message on the evening it stopped.

Everything here is read-only. The Hermes kanban kernel owns the readiness
rule and this project uses that kernel rather than editing it, so a
stranded task is made *visible* instead of being forced to run - its input
genuinely does not exist, and running it anyway would produce confident
garbage, which is the one outcome a verify-gated system exists to prevent.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping, Optional

# A parent in one of these states will never complete, so anything waiting
# on it is waiting forever.
DEAD_STATUSES = frozenset({"cancelled", "abandoned", "rejected"})

# Statuses that mean "this task still wants to run".
WAITING_STATUSES = frozenset({"todo", "ready", "blocked"})

# `ended_at` is epoch seconds. Anything older than this predates the
# project, so it is fixture or corrupt data rather than a real run, and
# reporting a stall from it is guessing - which is how a test board
# produced "nothing has finished for 497409h".
PLAUSIBLE_EPOCH_FLOOR = 1_600_000_000  # 2020-09-13

DEFAULT_QUIET_FOR = 6 * 3600
DEFAULT_REPEAT_AFTER = 6 * 3600


@dataclass(frozen=True)
class Stranded:
    task_id: str
    title: str
    blocked_by_id: str
    blocked_by_title: str
    blocked_by_status: str


@dataclass(frozen=True)
class Stall:
    is_stalled: bool
    waiting: int
    quiet_seconds: int


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[Any]:
    cursor = conn.execute(sql, args)
    cursor.row_factory = sqlite3.Row
    return cursor.fetchall()


def stranded(conn: sqlite3.Connection) -> list[Stranded]:
    """Waiting tasks that can never become ready, and what is holding them.

    Walks ancestors rather than only direct parents: `Integrate payments`
    was two steps from the cancelled task and a one-level check missed it.
    A link to a task that does not exist counts too - it is unsatisfiable
    in exactly the same way, and just as quietly.
    """
    status = {str(r[0]): str(r[1]) for r in
              conn.execute("SELECT id, status FROM tasks")}
    title = {str(r[0]): str(r[1] or "") for r in
             conn.execute("SELECT id, title FROM tasks")}
    parents: dict[str, list[str]] = {}
    for parent_id, child_id in conn.execute(
            "SELECT parent_id, child_id FROM task_links"):
        parents.setdefault(str(child_id), []).append(str(parent_id))

    found: list[Stranded] = []
    for task_id, state in sorted(status.items()):
        if state not in WAITING_STATUSES:
            continue
        # Breadth-first over ancestors, with a seen-set so a cycle in the
        # graph cannot hang the walk.
        seen: set[str] = {task_id}
        queue = list(parents.get(task_id, ()))
        while queue:
            ancestor = queue.pop(0)
            if ancestor in seen:
                continue
            seen.add(ancestor)
            ancestor_state = status.get(ancestor)
            if ancestor_state is None:
                found.append(Stranded(task_id, title.get(task_id, ""), ancestor,
                                      title.get(ancestor, ancestor), "missing"))
                break
            if ancestor_state in DEAD_STATUSES:
                found.append(Stranded(task_id, title.get(task_id, ""), ancestor,
                                      title.get(ancestor, ""), ancestor_state))
                break
            queue.extend(parents.get(ancestor, ()))
    return found


def stalled(conn: sqlite3.Connection, *, now: int,
            quiet_for: int = DEFAULT_QUIET_FOR) -> Stall:
    """Work is waiting, and nothing has finished a run for `quiet_for`.

    A running task is never a stall however long it takes - that is work in
    progress, and calling it a stall would train the operator to ignore the
    message. An empty queue is not a stall either; it is a finished board.
    """
    waiting = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE status IN (%s)"
        % ",".join("?" * len(WAITING_STATUSES)),
        tuple(sorted(WAITING_STATUSES)),
    ).fetchone()[0]
    running = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE status = 'running'").fetchone()[0]
    latest = conn.execute(
        "SELECT MAX(ended_at) FROM task_runs").fetchone()[0]

    # No run history at all is not evidence of a stall: a board created a
    # moment ago with work queued looks identical to one abandoned for a
    # year. Measuring from the epoch produced "nothing has finished for
    # 497408h", which is the shape of an alert nobody will ever trust.
    if not latest or int(latest) < PLAUSIBLE_EPOCH_FLOOR:
        return Stall(is_stalled=False, waiting=int(waiting), quiet_seconds=0)
    quiet = int(now) - int(latest)
    is_stalled = bool(waiting) and not running and quiet >= int(quiet_for)
    return Stall(is_stalled=is_stalled, waiting=int(waiting),
                 quiet_seconds=int(quiet))


def digest(conn: sqlite3.Connection, *, now: int,
           quiet_for: int = DEFAULT_QUIET_FOR) -> Optional[str]:
    """One short message, or None when there is nothing worth saying.

    Returning None for a healthy board matters: a periodic check that
    always says something is one the operator stops reading, and then the
    real notice is lost among them.
    """
    lines: list[str] = []

    blocked = stranded(conn)
    if blocked:
        lines.append(f"{len(blocked)} task(s) can never run:")
        for item in blocked[:6]:
            lines.append(
                f"  - {item.title or item.task_id} waits on "
                f"{item.blocked_by_title or item.blocked_by_id!r} "
                f"({item.blocked_by_status})")
        if len(blocked) > 6:
            lines.append(f"  … and {len(blocked) - 6} more")

    state = stalled(conn, now=now, quiet_for=quiet_for)
    if state.is_stalled:
        hours = state.quiet_seconds // 3600
        lines.append(
            f"{state.waiting} task(s) waiting and nothing has finished for "
            f"{hours}h.")

    return "\n".join(lines) if lines else None


def should_send(state: Mapping[str, Any], message: str, *, now: int,
                repeat_after: int = DEFAULT_REPEAT_AFTER,
                ) -> tuple[bool, dict[str, Any]]:
    """Whether to send this digest now, and the state to remember.

    Announced once is the bug that let three days pass. Announced every
    tick is the opposite bug, and ends the same way - ignored. So an
    unchanged situation repeats on a bounded cadence, while a *different*
    situation is news and goes out at once.

    State is plain JSON-able data so the caller can keep it in a file
    rather than requiring a new board table.
    """
    previous = str((state or {}).get("message") or "")
    last_sent = (state or {}).get("sent_at")
    try:
        last_sent = int(last_sent)
    except (TypeError, ValueError):
        last_sent = None

    changed = message != previous
    due = last_sent is None or int(now) - last_sent >= int(repeat_after)
    send = bool(message) and (changed or due)
    if not send:
        return False, dict(state or {})
    return True, {"message": message, "sent_at": int(now)}
