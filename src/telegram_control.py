"""Confirmed write commands for Telegram, separated from HTTPS transport.

The daemon authenticates a chat and hands it text. This module validates that
text against a local operator policy, then delegates every mutation to
board-owned transactional primitives. It accepts neither shell commands nor
verification settings from Telegram.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import board
import telegram_read_surface


PENDING_SECONDS = 15 * 60
READ_COMMANDS = {"/status", "/task", "/logs", "/help"}


@dataclass(frozen=True)
class VerifyProfile:
    name: str
    command: str
    timeout: int


@dataclass(frozen=True)
class WorkspacePolicy:
    alias: str
    path: Path
    profile: VerifyProfile


@dataclass(frozen=True)
class IntakePolicy:
    workspaces: Mapping[str, WorkspacePolicy]


def load_policy(path: Path | str) -> IntakePolicy:
    """Load a local, operator-owned workspace/profile allowlist."""
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read intake policy: {type(exc).__name__}") from exc
    profiles_raw = raw.get("verify_profiles")
    workspaces_raw = raw.get("workspaces")
    if not isinstance(profiles_raw, dict) or not isinstance(workspaces_raw, dict):
        raise ValueError("intake policy needs verify_profiles and workspaces objects")

    profiles: dict[str, VerifyProfile] = {}
    for name, spec in profiles_raw.items():
        if not isinstance(name, str) or not isinstance(spec, dict):
            raise ValueError("invalid verify profile")
        command = spec.get("command")
        timeout = spec.get("timeout")
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"profile {name!r} has no command")
        if not isinstance(timeout, int) or timeout <= 0:
            raise ValueError(f"profile {name!r} has no positive timeout")
        profiles[name] = VerifyProfile(name, command, timeout)

    workspaces: dict[str, WorkspacePolicy] = {}
    for alias, spec in workspaces_raw.items():
        if not isinstance(alias, str) or not alias or not isinstance(spec, dict):
            raise ValueError("invalid workspace policy")
        configured = spec.get("path")
        profile_name = spec.get("profile")
        if not isinstance(configured, str) or not isinstance(profile_name, str):
            raise ValueError(f"workspace {alias!r} is incomplete")
        if profile_name not in profiles:
            raise ValueError(f"workspace {alias!r} names an unknown profile")
        workspace = Path(configured)
        if not workspace.is_absolute() or not workspace.exists() or not (workspace / ".git").exists():
            raise ValueError(f"workspace {alias!r} is not an existing Git worktree")
        workspaces[alias] = WorkspacePolicy(alias, workspace.resolve(), profiles[profile_name])
    if not workspaces:
        raise ValueError("intake policy has no workspaces")
    return IntakePolicy(workspaces)


class TelegramControl:
    """Combined read router and constrained confirmed write router."""

    def __init__(self, policy: IntakePolicy, *, pending_seconds: int = PENDING_SECONDS) -> None:
        if pending_seconds <= 0:
            raise ValueError("pending action lifetime must be positive")
        self._policy = policy
        self._pending_seconds = pending_seconds

    def _pending(
        self,
        conn,
        *,
        chat_id: str,
        action: str,
        payload: dict[str, Any],
        now: Optional[int] = None,
    ) -> str:
        current = int(time.time()) if now is None else int(now)
        action_id = f"p_{secrets.token_hex(8)}"
        board.create_pending_action(
            conn, action_id=action_id, chat_id=chat_id, action=action,
            payload=payload, expires_at=current + self._pending_seconds,
        )
        return action_id

    def dispatch(
        self,
        text: str,
        *,
        chat_id: str,
        board_path: Path | str,
        ledger_path: Path | str,
        runs_root: Path | str,
    ) -> str:
        """Return one honest command response; no command accepts shell text."""
        parts = text.strip().split(maxsplit=2)
        command = parts[0].lower() if parts else "/help"
        if command in READ_COMMANDS:
            return telegram_read_surface.dispatch_command(
                text, board_path=board_path, ledger_path=ledger_path, runs_root=runs_root
            )
        conn = board.connect(Path(board_path))
        try:
            if command == "/run":
                if len(parts) != 3 or parts[1] not in self._policy.workspaces:
                    return "Usage: /run <workspace-alias> <prompt>"
                workspace = self._policy.workspaces[parts[1]]
                action_id = self._pending(
                    conn,
                    chat_id=chat_id,
                    action="run",
                    payload={
                        "title": f"Telegram: {workspace.alias}",
                        "prompt": parts[2],
                        "workspace": str(workspace.path),
                        "verify_command": workspace.profile.command,
                        "verify_timeout": workspace.profile.timeout,
                    },
                )
                return f"Pending intake {action_id}. Confirm with /confirm {action_id}"
            if command == "/retry":
                if len(parts) != 2:
                    return "Usage: /retry <task-id>"
                action_id = self._pending(
                    conn, chat_id=chat_id, action="retry", payload={"task_id": parts[1]}
                )
                return f"Pending retry {action_id}. Confirm with /confirm {action_id}"
            if command == "/confirm":
                if len(parts) != 2:
                    return "Usage: /confirm <request-id>"
                result = board.confirm_pending_action(conn, action_id=parts[1], chat_id=chat_id)
                if result.changed:
                    return f"Confirmed {parts[1]}: task {result.task_id} is {result.status}"
                return f"Confirmation refused: {result.status} ({result.detail})"
            if command == "/cancel":
                if len(parts) != 2:
                    return "Usage: /cancel <task-id>"
                result = board.cancel_task(conn, parts[1])
                if result.changed:
                    return f"Cancelled task {result.task_id}"
                return f"Cancel refused: {result.status} ({result.detail})"
            return f"Unknown command: {command}"
        finally:
            conn.close()
