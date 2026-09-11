"""Tri-AI board — the Hermes kanban kernel plus a verify-command oracle.

The board substrate is ``hermes_cli/kanban_db.py``: an existing, production
kernel that already implements atomic claim, lease/heartbeat/reclaim, and
dependency-gated DAG promotion. None of that is reimplemented here.

This module adds exactly the one thing the kernel is missing for Tri-AI's
central rule — *a delegated task is accepted or rejected by a verification
command's exit code, never by the agent's own report* — namely a
``verify_command`` (and its timeout and expected artifacts) per task.

**The kernel is used, never edited.** The Hermes install updates itself: every
package in its tree has a ``*.hermes-update-staging`` sibling, so an in-place
patch to ``kanban_db.py`` would be silently reverted by the next update. The
additive migration therefore runs from here, against Tri-AI's own board file,
through the kernel's own ``add_column_if_missing`` helper. It survives Hermes
updates because the kernel's own migration pass is purely additive and the
``tasks`` table is not in its ``_REBUILD_SPECS`` drift-rebuild list (only
``task_events`` / ``task_comments`` / ``task_runs`` / ``kanban_notify_subs``
are ever rebuilt from canonical DDL).

Paths, both overridable by environment variable:

* ``TRIAI_HERMES_HOME`` — the Hermes checkout to import the kernel from.
* ``TRIAI_BOARD_DB``    — the Tri-AI board file (default ``~/.tri-ai/board.db``).
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

DEFAULT_HERMES_HOME = (
    Path.home() / "AppData" / "Local" / "hermes" / "hermes-agent"
)
DEFAULT_BOARD_DB = Path.home() / ".tri-ai" / "board.db"

# The columns this project adds to the kernel's ``tasks`` table. Name -> DDL,
# in the exact shape ``add_column_if_missing`` expects.
VERIFY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("verify_command", "verify_command TEXT"),
    ("verify_timeout", "verify_timeout INTEGER"),
    ("expected_artifacts", "expected_artifacts TEXT"),
)

_kanban_module = None


def hermes_home() -> Path:
    """Where the Hermes kernel is imported from."""
    override = os.environ.get("TRIAI_HERMES_HOME", "").strip()
    return Path(override) if override else DEFAULT_HERMES_HOME


def board_db_path() -> Path:
    """The Tri-AI board file."""
    override = os.environ.get("TRIAI_BOARD_DB", "").strip()
    return Path(override) if override else DEFAULT_BOARD_DB


def kanban():
    """Import and return ``hermes_cli.kanban_db``, cached.

    ``HERMES_KANBAN_DB`` is pinned to the Tri-AI board before import so that
    any kernel call made without an explicit path resolves to our board and
    never to the operator's own default Hermes board.
    """
    global _kanban_module
    if _kanban_module is not None:
        return _kanban_module
    home = hermes_home()
    if not (home / "hermes_cli" / "kanban_db.py").exists():
        raise RuntimeError(
            f"Hermes kernel not found under {home}. "
            "Set TRIAI_HERMES_HOME to the hermes-agent checkout."
        )
    if str(home) not in sys.path:
        sys.path.insert(0, str(home))
    # Assigned, not setdefault: the Hermes dispatcher injects HERMES_KANBAN_DB into
    # every worker it spawns (kanban_db.py's worker env setup), so a setdefault here
    # would silently keep the dispatcher's board. Tri-AI's board must win in its own
    # process, whoever started it.
    os.environ["HERMES_KANBAN_DB"] = str(board_db_path())
    import hermes_cli.kanban_db as kanban_db  # noqa: E402

    _kanban_module = kanban_db
    return kanban_db


class SchemaCollision(RuntimeError):
    """A verify column exists on ``tasks`` but is not the column we expect."""


def _column_types(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    """``{column_name: declared_type}`` for ``table``, types upper-cased."""
    return {
        row["name"]: (row["type"] or "").upper()
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def migrate(conn: sqlite3.Connection) -> dict[str, bool]:
    """Add the verify columns to ``tasks``. Idempotent.

    Returns ``{column: added_by_this_call}``. A second call on the same
    database returns all-``False`` and raises nothing — that no-op is the
    property the migration is tested for.

    Idempotency here has to mean more than "did not raise". The kernel's
    ``add_column_if_missing`` swallows SQLite's ``duplicate column name``
    error, so a column of the *same name but a different type* — added by a
    future Hermes release, or by anything else touching this table — would be
    silently accepted and then read as if it were ours. That is the failure
    this project exists to prevent: nothing errors, the board keeps working,
    and verification quietly means something else. So the declared type is
    checked both before and after the additive pass, and a mismatch raises
    ``SchemaCollision`` rather than proceeding.
    """
    kanban()  # ensures hermes_home() is on sys.path
    from hermes_cli.sqlite_util import add_column_if_missing

    expected = {
        column: ddl.split(None, 1)[1].strip().upper()
        for column, ddl in VERIFY_COLUMNS
    }

    def assert_no_collision(stage: str) -> None:
        present = _column_types(conn, "tasks")
        for column, want in expected.items():
            got = present.get(column)
            if got is not None and got != want:
                raise SchemaCollision(
                    f"tasks.{column} is declared {got!r}, expected {want!r} "
                    f"({stage}). Something else owns this column name — "
                    f"do not write verify data through it."
                )

    assert_no_collision("before migrating")

    added: dict[str, bool] = {}
    for column, ddl in VERIFY_COLUMNS:
        added[column] = add_column_if_missing(conn, "tasks", column, ddl)

    # Re-check after the pass: a column this call did *not* add could have been
    # created concurrently by another migrator, and add_column_if_missing would
    # have swallowed that race exactly as it swallows a legitimate no-op.
    assert_no_collision("after migrating")
    final = _column_types(conn, "tasks")
    missing = [c for c in expected if c not in final]
    if missing:
        raise SchemaCollision(f"verify columns absent after migrating: {missing}")

    # Tri-AI's own table, not the kernel's — created here rather than by an
    # ALTER, so no collision check applies. CREATE TABLE IF NOT EXISTS is
    # idempotent on the same terms as the column adds above.
    conn.execute(QUARANTINE_DDL)
    conn.execute(WORKTREE_DDL)
    conn.execute(ENVIRONMENT_BACKOFF_DDL)
    conn.execute(PENDING_ACTIONS_DDL)

    conn.commit()
    return added


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open the Tri-AI board, running both the kernel's schema pass and ours."""
    kb = kanban()
    path = Path(db_path) if db_path is not None else board_db_path()
    conn = kb.connect(path)
    migrate(conn)
    return conn


def create_task(
    conn: sqlite3.Connection,
    *,
    title: str,
    prompt: str,
    verify_command: str,
    repo: Optional[Path | str] = None,
    verify_timeout: Optional[int] = None,
    expected_artifacts: Iterable[str] = (),
    parents: Iterable[str] = (),
    workspace_kind: Optional[str] = None,
    workspace_path: Optional[Path | str] = None,
    branch_name: Optional[str] = None,
    **kernel_kwargs: Any,
) -> str:
    """Create a verify-gated task row and return its id.

    A task without a verify command is rejected here, at write time, with a
    ``ValueError`` — never discovered later at claim or run time. That is the
    whole point: an unverifiable task does not belong on the board.

    ``repo`` is the Phase 2 shorthand for a ``dir`` workspace. Graph writers
    can instead provide an explicit kernel workspace kind/path and, for a
    worktree, branch. Both forms are deliberately narrow arguments rather than
    planner input flowing through arbitrary kernel keyword arguments.
    """
    kb = kanban()
    if not (verify_command or "").strip():
        raise ValueError(
            f"verify_command is required (task {title!r}). If no command can "
            "prove this task worked, it does not belong on the board."
        )
    if verify_timeout is not None and int(verify_timeout) <= 0:
        raise ValueError("verify_timeout must be a positive number of seconds")

    artifacts = [str(a) for a in expected_artifacts]
    if repo is not None and (
        workspace_kind is not None or workspace_path is not None or branch_name is not None
    ):
        raise ValueError("repo cannot be combined with explicit workspace fields")
    if workspace_kind is None and (workspace_path is not None or branch_name is not None):
        raise ValueError("workspace_kind is required with explicit workspace fields")
    if workspace_kind is not None:
        if workspace_kind not in {"dir", "worktree", "scratch"}:
            raise ValueError(f"unsupported workspace_kind: {workspace_kind!r}")
        if workspace_path is None:
            raise ValueError("workspace_path is required with workspace_kind")
        path = Path(workspace_path)
        if not path.is_absolute():
            raise ValueError("workspace_path must be absolute")
        if branch_name and workspace_kind != "worktree":
            raise ValueError("branch_name is only valid for worktree workspaces")
        if workspace_kind == "worktree" and not (branch_name or "").strip():
            raise ValueError("branch_name is required for worktree workspaces")
        kernel_kwargs["workspace_kind"] = workspace_kind
        kernel_kwargs["workspace_path"] = str(path)
        if branch_name:
            kernel_kwargs["branch_name"] = str(branch_name)
    elif repo is not None:
        kernel_kwargs.setdefault("workspace_kind", "dir")
        kernel_kwargs.setdefault("workspace_path", str(repo))

    # One transaction, not two. The kernel's create_task commits on its own, so
    # calling it first and setting the verify columns afterwards would leave a
    # window in which the row is visible as `ready` with verify_command NULL —
    # an unverifiable task, claimable by any worker polling at that instant.
    # create_task opts into savepoint nesting (write_txn(conn, allow_nested=True))
    # precisely so graph builders can compose under one outer commit.
    with kb.write_txn(conn, allow_nested=True):
        # The kernel's own duplicate guard returns the EXISTING task id when a
        # non-archived row already carries this idempotency_key (kanban_db.py
        # create_task fast path) — a re-submission, not an insert. Detect it
        # here, inside the same write lock, so this call knows whether it
        # created the row: the verify columns must not be re-written onto an
        # existing task, because a re-run of a queue line whose verify /
        # verify_timeout / expected_artifacts changed would then refresh ONLY
        # those three columns while title, prompt, workspace and
        # max_runtime_seconds stay stale — a fresh oracle bolted onto an old
        # workspace, reported as success. That split-state is the exact
        # mismatched-gate hazard this project exists to catch. Idempotency is
        # a no-op, not a partial update: to change a task, delete it and
        # re-assign. (write_txn is IMMEDIATE, so no concurrent writer can
        # interleave a same-key row between this lookup and create_task.)
        idem_key = kernel_kwargs.get("idempotency_key")
        already_present = idem_key is not None and conn.execute(
            "SELECT 1 FROM tasks WHERE idempotency_key = ? "
            "AND status != 'archived' LIMIT 1",
            (idem_key,),
        ).fetchone() is not None
        task_id = kb.create_task(
            conn,
            title=title,
            body=prompt,
            parents=list(parents),
            **kernel_kwargs,
        )
        if not already_present:
            conn.execute(
                "UPDATE tasks SET verify_command = ?, verify_timeout = ?, "
                "expected_artifacts = ? WHERE id = ?",
                (
                    verify_command,
                    int(verify_timeout) if verify_timeout is not None else None,
                    json.dumps(artifacts),
                    task_id,
                ),
            )
    return task_id


def verify_spec(conn: sqlite3.Connection, task_id: str) -> Optional[dict[str, Any]]:
    """Return the recorded oracle for ``task_id``, or ``None`` if unknown."""
    row = conn.execute(
        "SELECT verify_command, verify_timeout, expected_artifacts, "
        "       workspace_path, body "
        "FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    raw = row["expected_artifacts"]
    return {
        "verify_command": row["verify_command"],
        "verify_timeout": row["verify_timeout"],
        "expected_artifacts": json.loads(raw) if raw else [],
        "repo": row["workspace_path"],
        "prompt": row["body"],
    }


# ---------------------------------------------------------------------------
# Ready-task selection
# ---------------------------------------------------------------------------


def ready_tasks(conn: sqlite3.Connection, *, now: Optional[int] = None) -> list[dict[str, Any]]:
    """Every currently reclaimable ready task, oldest-first within priority.

    ``verify_command`` must be present: an unverifiable task does not belong
    on the board (``create_task`` enforces that at write time; this re-checks
    rows written through lower-level APIs).
    """
    current_time = int(time.time()) if now is None else int(now)
    rows = conn.execute(
        "SELECT id, title, workspace_path, verify_command, verify_timeout, "
        "       workspace_kind, branch_name "
        "FROM tasks "
        "WHERE status = 'ready' AND claim_lock IS NULL "
        "  AND verify_command IS NOT NULL "
        "  AND NOT EXISTS ("
        "      SELECT 1 FROM triai_environment_backoff AS backoff "
        "      WHERE backoff.task_id = tasks.id AND backoff.eligible_at > ?"
        "  ) "
        "ORDER BY priority DESC, created_at ASC"
        , (current_time,)
    ).fetchall()
    return [dict(r) for r in rows]


def worktree_for_task(conn: sqlite3.Connection, task_id: str) -> Optional[dict[str, Any]]:
    """The durable ownership record for a Tri-AI-created worktree, if any."""
    row = conn.execute(
        "SELECT * FROM triai_worktrees WHERE task_id = ?", (task_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def list_worktrees(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every Tri-AI-owned worktree record, oldest first."""
    return [dict(row) for row in conn.execute(
        "SELECT * FROM triai_worktrees ORDER BY created_at, task_id"
    ).fetchall()]


def record_worktree(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    source_path: Path | str,
    target_path: Path | str,
    branch_name: str,
) -> None:
    """Record ownership and move a ready task to its materialized checkout."""
    kb = kanban()
    source = str(Path(source_path).resolve())
    target = str(Path(target_path).resolve())
    with kb.write_txn(conn, allow_nested=True):
        existing = conn.execute(
            "SELECT source_path, target_path, branch_name FROM triai_worktrees "
            "WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        expected = (source, target, branch_name)
        if existing is not None:
            actual = (existing["source_path"], existing["target_path"], existing["branch_name"])
            if actual != expected:
                raise RuntimeError(
                    f"worktree ownership collision for task {task_id}: "
                    f"recorded {actual!r}, requested {expected!r}"
                )
        else:
            conn.execute(
                "INSERT INTO triai_worktrees "
                "(task_id, source_path, target_path, branch_name, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, source, target, branch_name, int(time.time())),
            )
        changed = conn.execute(
            "UPDATE tasks SET workspace_path = ? WHERE id = ? AND status = 'ready' "
            "AND claim_lock IS NULL",
            (target, task_id),
        ).rowcount
        if changed != 1:
            raise RuntimeError(
                f"task {task_id} ceased to be ready while recording its worktree"
            )


# ---------------------------------------------------------------------------
# Reclaim
# ---------------------------------------------------------------------------
#
# The kernel decides "did my worker survive the kill signal?" from the
# exception ``os.kill`` raises: ``ProcessLookupError`` means the process was
# already gone, which counts as a successful termination. On Windows
# ``os.kill`` never raises that. For a dead PID it raises ``PermissionError``
# (WinError 5) and for one that never existed ``OSError`` (WinError 87) — both
# of which ``_terminate_reclaimed_worker`` reads as "we could not kill it", so
# ``_worker_survived_termination`` returns True and the reclaim is deferred.
# Every subsequent tick defers again, so on Windows a crashed worker's task
# stays ``running`` forever and is never reclaimed.
#
# The kernel exposes a ``signal_fn`` hook on exactly the paths that need it.
# Supplying one that raises ``ProcessLookupError`` when the process is already
# gone gives Windows the POSIX semantics the kernel is written against, without
# editing the kernel and without weakening the guard: a worker that is genuinely
# still alive is still signalled for real, and still defers the reclaim if it
# survives.


def posix_semantics_signal(pid: int, sig: int) -> None:
    """``os.kill`` with the "already gone" case reported the POSIX way."""
    kb = kanban()
    if not kb._pid_alive(pid):
        raise ProcessLookupError(pid)
    os.kill(int(pid), sig)


def release_stale_claims(conn: sqlite3.Connection, *, signal_fn=None) -> int:
    """Kernel ``release_stale_claims`` with platform-correct liveness.

    Returns the number of claims actually reclaimed. A claim whose worker is
    still alive is extended, not reclaimed, and is not counted — that deferral
    is what stops a second worker being spawned beside a live one.
    """
    kb = kanban()
    return kb.release_stale_claims(
        conn, signal_fn=signal_fn if signal_fn is not None else posix_semantics_signal
    )


# ---------------------------------------------------------------------------
# Workspace quarantine
# ---------------------------------------------------------------------------
#
# When a verify command times out and its process tree survives the kill, the
# worker must stop — but stopping is not enough on its own. The worker's claim
# expires, ``release_stale_claims`` correctly reclaims the task to ``ready``
# (that is Phase 1 working exactly as designed), and the next worker runs
# against the same repository with the same unaccounted-for writer still in it.
#
# So the stop is recorded somewhere reclaim cannot reach: a row keyed by the
# resolved workspace path. Every worker checks it before claiming; only an
# operator clears it. Safety state has to be at least as durable as the
# mechanism that undoes it.

QUARANTINE_DDL = """
CREATE TABLE IF NOT EXISTS triai_quarantine (
    workspace   TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    run_id      INTEGER,
    reason      TEXT NOT NULL,
    detail      TEXT,
    created_at  INTEGER NOT NULL
)
"""

WORKTREE_DDL = """
CREATE TABLE IF NOT EXISTS triai_worktrees (
    task_id      TEXT PRIMARY KEY,
    source_path  TEXT NOT NULL,
    target_path  TEXT NOT NULL UNIQUE,
    branch_name  TEXT NOT NULL,
    created_at   INTEGER NOT NULL
)
"""

ENVIRONMENT_BACKOFF_DDL = """
CREATE TABLE IF NOT EXISTS triai_environment_backoff (
    task_id      TEXT PRIMARY KEY,
    attempts     INTEGER NOT NULL,
    eligible_at  INTEGER NOT NULL,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
)
"""

PENDING_ACTIONS_DDL = """
CREATE TABLE IF NOT EXISTS triai_pending_actions (
    id          TEXT PRIMARY KEY,
    chat_id     TEXT NOT NULL,
    action      TEXT NOT NULL,
    payload     TEXT NOT NULL,
    status      TEXT NOT NULL,
    result_id   TEXT,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    confirmed_at INTEGER
)
"""


@dataclass(frozen=True)
class ControlResult:
    """The durable result of a Telegram-originated board control action."""

    changed: bool
    status: str
    task_id: Optional[str] = None
    detail: str = ""


def create_pending_action(
    conn: sqlite3.Connection,
    *,
    action_id: str,
    chat_id: str,
    action: str,
    payload: Mapping[str, Any],
    expires_at: int,
) -> ControlResult:
    """Persist a confirmation request without creating or changing a task."""
    if action not in {"run", "retry"}:
        raise ValueError(f"unsupported pending action: {action!r}")
    if not action_id or not chat_id:
        raise ValueError("pending action id and chat id are required")
    if int(expires_at) <= int(time.time()):
        raise ValueError("pending action expiry must be in the future")
    kb = kanban()
    with kb.write_txn(conn):
        existing = conn.execute(
            "SELECT status, result_id FROM triai_pending_actions WHERE id = ?",
            (action_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO triai_pending_actions "
                "(id, chat_id, action, payload, status, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (
                    action_id, chat_id, action, json.dumps(dict(payload)),
                    int(time.time()), int(expires_at),
                ),
            )
            return ControlResult(True, "pending", detail=action)
        return ControlResult(
            False, str(existing["status"]), existing["result_id"],
            "pending action id already exists",
        )


def pending_action(conn: sqlite3.Connection, action_id: str) -> Optional[dict[str, Any]]:
    """Return a durable confirmation request, decoded, or None."""
    row = conn.execute(
        "SELECT * FROM triai_pending_actions WHERE id = ?", (action_id,)
    ).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["payload"] = json.loads(out["payload"])
    return out


def _retry_task_in_txn(conn: sqlite3.Connection, task_id: str) -> ControlResult:
    """Requeue one terminal task under the caller's existing write lock."""
    row = conn.execute(
        "SELECT status, claim_lock, workspace_path, verify_command, verify_timeout "
        "FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        return ControlResult(False, "missing", task_id, "task does not exist")
    if row["status"] not in {"failed", "blocked"} or row["claim_lock"] is not None:
        return ControlResult(False, str(row["status"]), task_id, "task is not retryable")
    workspace = row["workspace_path"]
    if workspace and is_quarantined(conn, workspace):
        return ControlResult(False, "quarantined", task_id, "workspace requires operator recovery")
    if not (row["verify_command"] or "").strip() or row["verify_timeout"] is None:
        return ControlResult(False, "unsafe", task_id, "task has no complete verify gate")
    changed = conn.execute(
        "UPDATE tasks SET status = 'todo', claim_lock = NULL, claim_expires = NULL, "
        "worker_pid = NULL, consecutive_failures = 0, last_failure_error = NULL "
        "WHERE id = ? AND status IN ('failed', 'blocked') AND claim_lock IS NULL",
        (task_id,),
    ).rowcount
    if changed != 1:
        return ControlResult(False, "raced", task_id, "task state changed during retry")
    kb = kanban()
    kb._append_event(conn, task_id, "retry_requested", {"source": "telegram"})
    return ControlResult(True, "todo", task_id)


def retry_task(conn: sqlite3.Connection, task_id: str) -> ControlResult:
    """Requeue an eligible terminal task without changing its verify oracle."""
    kb = kanban()
    with kb.write_txn(conn):
        result = _retry_task_in_txn(conn, task_id)
    if result.changed:
        kb.recompute_ready(conn)
        row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return ControlResult(True, str(row["status"]), task_id)
    return result


def confirm_pending_action(
    conn: sqlite3.Connection,
    *,
    action_id: str,
    chat_id: str,
    now: Optional[int] = None,
) -> ControlResult:
    """Apply exactly one pending action for its originating authorized chat."""
    current = int(time.time()) if now is None else int(now)
    kb = kanban()
    needs_recompute = False
    with kb.write_txn(conn, allow_nested=True):
        row = conn.execute(
            "SELECT * FROM triai_pending_actions WHERE id = ?", (action_id,)
        ).fetchone()
        if row is None:
            return ControlResult(False, "missing", detail="confirmation request does not exist")
        if str(row["chat_id"]) != str(chat_id):
            return ControlResult(False, "forbidden", detail="confirmation belongs to another chat")
        if row["status"] == "confirmed":
            return ControlResult(False, "confirmed", row["result_id"], "already confirmed")
        if row["status"] != "pending":
            return ControlResult(False, str(row["status"]), row["result_id"], "request is not pending")
        if int(row["expires_at"]) <= current:
            conn.execute(
                "UPDATE triai_pending_actions SET status = 'expired' "
                "WHERE id = ? AND status = 'pending'",
                (action_id,),
            )
            return ControlResult(False, "expired", detail="confirmation request expired")

        payload = json.loads(row["payload"])
        if row["action"] == "run":
            task_id = create_task(
                conn,
                title=str(payload["title"]),
                prompt=str(payload["prompt"]),
                verify_command=str(payload["verify_command"]),
                verify_timeout=int(payload["verify_timeout"]),
                repo=str(payload["workspace"]),
                idempotency_key=f"telegram-pending:{action_id}",
            )
            result = ControlResult(True, "confirmed", task_id)
        elif row["action"] == "retry":
            result = _retry_task_in_txn(conn, str(payload["task_id"]))
            needs_recompute = result.changed
        else:
            return ControlResult(False, "unsafe", detail="unknown stored action")

        conn.execute(
            "UPDATE triai_pending_actions SET status = 'confirmed', result_id = ?, "
            "confirmed_at = ? WHERE id = ? AND status = 'pending'",
            (result.task_id, current, action_id),
        )
    if needs_recompute:
        kb.recompute_ready(conn)
        row = conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (result.task_id,)
        ).fetchone()
        return ControlResult(True, str(row["status"]), result.task_id)
    return result


def cancel_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    signal_fn=None,
    wait_seconds: float = 5.0,
) -> ControlResult:
    """Cancel one exact running claim, refusing to overwrite a completed race."""
    row = conn.execute(
        "SELECT status, claim_lock, worker_pid, current_run_id FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        return ControlResult(False, "missing", task_id, "task does not exist")
    if row["status"] != "running" or row["claim_lock"] is None:
        return ControlResult(False, str(row["status"]), task_id, "task is not running")

    pid = row["worker_pid"]
    if pid is None:
        return ControlResult(False, "unknown", task_id, "running task has no worker PID")
    try:
        (signal_fn or posix_semantics_signal)(int(pid), 15)
    except ProcessLookupError:
        pass
    except OSError as exc:
        return ControlResult(False, "survived", task_id, f"worker termination failed: {type(exc).__name__}")

    # A completed race is handled by the compare-and-swap below. A still-live
    # worker is different: changing its board row while it can still write or
    # complete is an unknown execution state, so retain the claim and refuse.
    if signal_fn is None:
        deadline = time.monotonic() + float(wait_seconds)
        while kb_pid_alive(int(pid)):
            if time.monotonic() >= deadline:
                return ControlResult(False, "survived", task_id, "worker did not terminate")
            time.sleep(0.05)

    kb = kanban()
    with kb.write_txn(conn):
        changed = conn.execute(
            "UPDATE tasks SET status = 'cancelled', claim_lock = NULL, "
            "claim_expires = NULL, worker_pid = NULL "
            "WHERE id = ? AND status = 'running' AND claim_lock IS ? "
            "AND current_run_id IS ?",
            (task_id, row["claim_lock"], row["current_run_id"]),
        ).rowcount
        if changed != 1:
            current = conn.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            return ControlResult(False, str(current["status"]), task_id, "task completed or changed first")
        kb._end_run(
            conn, task_id, outcome="cancelled", status="cancelled",
            error="telegram_cancel", metadata={"source": "telegram"},
        )
        kb._append_event(conn, task_id, "cancelled", {"source": "telegram"})
    return ControlResult(True, "cancelled", task_id)


def kb_pid_alive(pid: int) -> bool:
    """Use the kernel's host-local liveness check without exposing it to callers."""
    return bool(kanban()._pid_alive(int(pid)))


def environment_backoff(conn: sqlite3.Connection, task_id: str) -> Optional[dict[str, Any]]:
    """Return the durable environment retry delay for a task, if one exists."""
    row = conn.execute(
        "SELECT * FROM triai_environment_backoff WHERE task_id = ?", (task_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def workspace_key(path: Path | str) -> str:
    r"""Canonical key for a workspace path.

    Resolved through symlinks and case-normalised, so ``C:\Repos\X``,
    ``c:/repos/x`` and a symlink to either are one workspace and cannot be
    used to sidestep a quarantine.
    """
    return os.path.normcase(os.path.realpath(str(path)))


def quarantine_workspace(
    conn: sqlite3.Connection,
    workspace: Path | str,
    *,
    task_id: str,
    reason: str,
    run_id: Optional[int] = None,
    detail: Optional[dict[str, Any]] = None,
) -> str:
    """Mark a workspace unusable until an operator clears it. Idempotent.

    Returns the key written. Re-quarantining an already-quarantined workspace
    keeps the *first* record: the original reason is the one that matters, and
    a later, vaguer report must not overwrite it.
    """
    kb = kanban()
    key = workspace_key(workspace)
    with kb.write_txn(conn):
        conn.execute(
            "INSERT OR IGNORE INTO triai_quarantine "
            "(workspace, task_id, run_id, reason, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                key,
                task_id,
                run_id,
                reason,
                json.dumps(detail or {}),
                int(time.time()),
            ),
        )
    return key


def is_quarantined(
    conn: sqlite3.Connection, workspace: Path | str
) -> Optional[dict[str, Any]]:
    """The quarantine record for ``workspace``, or ``None`` if it is usable."""
    row = conn.execute(
        "SELECT * FROM triai_quarantine WHERE workspace = ?",
        (workspace_key(workspace),),
    ).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["detail"] = json.loads(out["detail"]) if out["detail"] else {}
    return out


def list_quarantines(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every quarantined workspace, newest first."""
    rows = conn.execute(
        "SELECT * FROM triai_quarantine ORDER BY created_at DESC"
    ).fetchall()
    out = []
    for row in rows:
        rec = dict(row)
        rec["detail"] = json.loads(rec["detail"]) if rec["detail"] else {}
        out.append(rec)
    return out


def clear_quarantine(conn: sqlite3.Connection, workspace: Path | str) -> bool:
    """Release a workspace. Operator action only — never called by a worker.

    Returns True if a record was removed.
    """
    kb = kanban()
    with kb.write_txn(conn):
        cur = conn.execute(
            "DELETE FROM triai_quarantine WHERE workspace = ?",
            (workspace_key(workspace),),
        )
    return cur.rowcount > 0
