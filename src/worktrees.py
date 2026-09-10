"""Task-owned linked checkout materialization for Phase 4 Slice 2.

Worktrees are created before a task is claimed. This module never removes one:
failure evidence stays on disk until an operator explicitly decides otherwise.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import board
import executor


@dataclass(frozen=True)
class Resolution:
    task: Optional[dict[str, Any]]
    reason: Optional[str] = None

    @property
    def ready(self) -> bool:
        return self.task is not None


def _target_path(source: Path, task_id: str) -> Path:
    """A deterministic sibling path, never inside the source checkout."""
    token = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:20]
    return source.parent / ".triai-worktrees" / token


def resolve_task(conn, task: dict[str, Any]) -> Resolution:
    """Return a materialized task or a non-mutating skip reason."""
    if task.get("workspace_kind") != "worktree":
        return Resolution(dict(task))

    task_id = str(task["id"])
    branch = str(task.get("branch_name") or "")
    try:
        # BEGIN IMMEDIATE prevents a second worker from claiming this ready
        # task between filesystem materialization and the board path update.
        # A process crash cannot atomically cover Git and SQLite, so a later
        # run may adopt only the exact deterministic target after proving it
        # belongs to this source and branch.
        with board.kanban().write_txn(conn):
            record = board.worktree_for_task(conn, task_id)
            if record is not None:
                source = Path(record["source_path"])
                target = Path(record["target_path"])
                if (
                    record["branch_name"] != branch
                    or board.workspace_key(task.get("workspace_path") or "") != board.workspace_key(target)
                    or not executor.verify_worktree(source, target, branch)
                ):
                    return Resolution(None, "recorded worktree ownership cannot be proven")
            else:
                source = Path(task.get("workspace_path") or "")
                if not source.is_absolute():
                    return Resolution(None, "worktree source path is not absolute")
                source = source.resolve()
                status, output = executor.git(["status", "--porcelain"], cwd=source)
                if status != 0 or output.strip():
                    return Resolution(None, "worktree source is not a clean git checkout")
                target = _target_path(source, task_id)
                if target.exists():
                    if not executor.verify_worktree(source, target, branch):
                        return Resolution(None, "existing worktree target cannot be proven owned")
                else:
                    executor.materialize_worktree(source, target, branch)
                board.record_worktree(
                    conn,
                    task_id=task_id,
                    source_path=source,
                    target_path=target,
                    branch_name=branch,
                )
    except (executor.WorktreeError, OSError, RuntimeError) as exc:
        return Resolution(None, f"worktree materialization skipped: {exc}")

    materialized = dict(task)
    materialized["workspace_path"] = str(target)
    return Resolution(materialized)
