"""Read-only command adapter for the future Telegram transport.

This module deliberately has no bot client, token lookup, HTTP dependency, or
mutation capability. A future Telegram transport may pass received command text
to ``dispatch_command`` and send its returned text. The adapter itself only
reads the Tri-AI board, ledger, and task-owned retained logs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional, Sequence

import board
import ledger


def _board_rows(board_path: Path | str) -> list[dict[str, Any]]:
    conn = board.connect(Path(board_path))
    try:
        return [dict(row) for row in conn.execute(
            "SELECT id, title, status, current_run_id, workspace_path, expected_artifacts FROM tasks "
            "ORDER BY priority DESC, created_at ASC"
        ).fetchall()]
    finally:
        conn.close()


def render_status(board_path: Path | str, ledger_path: Path | str) -> str:
    """Render current board state and ledger count without synthesizing either."""
    rows = _board_rows(board_path)
    entries = ledger.read_entries(Path(ledger_path))
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    summary = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    lines = [f"Tri-AI status: tasks={len(rows)} ({summary or 'none'})", f"Ledger entries: {len(entries)}"]
    lines.extend(f"[{row['status']}] {row['id']} {row['title']}" for row in rows)
    return "\n".join(lines)


def render_task(board_path: Path | str, ledger_path: Path | str, task_id: str) -> str:
    """Render one board row and exactly its ledger entries."""
    row = next((item for item in _board_rows(board_path) if item["id"] == task_id), None)
    if row is None:
        return f"Unknown task: {task_id}"
    entries = [entry for entry in ledger.read_entries(Path(ledger_path)) if entry.get("task_id") == task_id]
    raw_artifacts = row.get("expected_artifacts") or "[]"
    try:
        artifacts = json.loads(raw_artifacts)
    except json.JSONDecodeError:
        artifacts = []
    target_files = ", ".join(str(item) for item in artifacts) or "none declared"
    lines = [
        f"Task {row['id']}: {row['title']}",
        f"Status: {row['status']}",
        f"Workspace: {row.get('workspace_path') or 'none recorded'}",
        f"Target files: {target_files}",
        f"Ledger entries: {len(entries)}",
    ]
    lines.extend(
        f"run={entry.get('run_id')} outcome={entry.get('outcome')} "
        f"verify_outcome={entry.get('verify_outcome')} verify_exit={entry.get('verify_exit')}"
        for entry in entries
    )
    return "\n".join(lines)


def _contained(path: str | None, runs_root: Path) -> Optional[Path]:
    if not path:
        return None
    candidate = Path(path).resolve()
    try:
        candidate.relative_to(runs_root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def render_logs(ledger_path: Path | str, task_id: str, *, runs_root: Path | str) -> str:
    """Render full retained task logs, refusing paths outside ``runs_root``."""
    entries = [entry for entry in ledger.read_entries(Path(ledger_path)) if entry.get("task_id") == task_id]
    if not entries:
        return f"No ledger entries for task {task_id}"
    latest = entries[-1]
    root = Path(runs_root)
    lines = [f"Logs for task {task_id}, run {latest.get('run_id')}"]
    for label in ("agent_log", "verify_log"):
        path = _contained(latest.get(label), root)
        if path is None:
            lines.append(f"{label}: unavailable (missing or outside task runs)")
            continue
        lines.append(f"--- {label} ---")
        lines.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(lines)


def dispatch_command(
    text: str,
    *,
    board_path: Path | str,
    ledger_path: Path | str,
    runs_root: Path | str,
) -> str:
    """Handle only read-only command forms for a future Telegram transport."""
    parts = text.strip().split(maxsplit=1)
    command = parts[0].lower() if parts else "/help"
    argument = parts[1].strip() if len(parts) == 2 else ""
    if command == "/status":
        return render_status(board_path, ledger_path)
    if command == "/task" and argument:
        return render_task(board_path, ledger_path, argument)
    if command == "/logs" and argument:
        return render_logs(ledger_path, argument, runs_root=runs_root)
    if command in {"/help", ""}:
        return "Commands: /status, /task <task_id>, /logs <task_id>"
    return f"Unknown or incomplete read-only command: {command}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render a read-only Tri-AI phone response locally.")
    parser.add_argument("command", help="/status, /task <id>, or /logs <id>")
    parser.add_argument("--board", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--runs-dir", required=True)
    args = parser.parse_args(argv)
    print(dispatch_command(args.command, board_path=args.board, ledger_path=args.ledger, runs_root=args.runs_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
