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


def migrate(conn: sqlite3.Connection) -> dict[str, bool]:
    """Add the verify columns to ``tasks``. Idempotent.

    Returns ``{column: added_by_this_call}``. A second call on the same
    database returns all-``False`` and raises nothing — that no-op is the
    property the migration is tested for.
    """
    kanban()  # ensures hermes_home() is on sys.path
    from hermes_cli.sqlite_util import add_column_if_missing

    added: dict[str, bool] = {}
    for column, ddl in VERIFY_COLUMNS:
        added[column] = add_column_if_missing(conn, "tasks", column, ddl)
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
    **kernel_kwargs: Any,
) -> str:
    """Create a verify-gated task row and return its id.

    A task without a verify command is rejected here, at write time, with a
    ``ValueError`` — never discovered later at claim or run time. That is the
    whole point: an unverifiable task does not belong on the board.

    ``repo`` is recorded through the kernel's own workspace fields
    (``workspace_kind='dir'`` + ``workspace_path``), not a parallel column.
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
    if repo is not None:
        kernel_kwargs.setdefault("workspace_kind", "dir")
        kernel_kwargs.setdefault("workspace_path", str(repo))

    # One transaction, not two. The kernel's create_task commits on its own, so
    # calling it first and setting the verify columns afterwards would leave a
    # window in which the row is visible as `ready` with verify_command NULL —
    # an unverifiable task, claimable by any worker polling at that instant.
    # create_task opts into savepoint nesting (write_txn(conn, allow_nested=True))
    # precisely so graph builders can compose under one outer commit.
    with kb.write_txn(conn):
        task_id = kb.create_task(
            conn,
            title=title,
            body=prompt,
            parents=list(parents),
            **kernel_kwargs,
        )
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
