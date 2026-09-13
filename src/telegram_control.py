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
import completion_report
import proposals
import telegram_read_surface


PENDING_SECONDS = 15 * 60
READ_COMMANDS = {"/status", "/task", "/logs"}
NATURAL_READ_COMMANDS = {
    "status": "/status",
    "board status": "/status",
    "show status": "/status",
    "what is the status": "/status",
    "what's the status": "/status",
}


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
    default_workspace: Optional[str]


@dataclass(frozen=True)
class CallbackResponse:
    text: str
    remove_buttons: bool


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
    default_workspace = raw.get("default_workspace")
    if default_workspace is None and len(workspaces) == 1:
        default_workspace = next(iter(workspaces))
    if default_workspace is not None:
        if not isinstance(default_workspace, str) or default_workspace not in workspaces:
            raise ValueError("default_workspace must name an allowed workspace alias")
    return IntakePolicy(workspaces, default_workspace)


class TelegramControl:
    """Combined read router and constrained confirmed write router."""

    def __init__(
        self,
        policy: Optional[IntakePolicy] = None,
        *,
        pending_seconds: int = PENDING_SECONDS,
        dashboard_url: Optional[str] = None,
    ) -> None:
        if pending_seconds <= 0:
            raise ValueError("pending action lifetime must be positive")
        self._policy = policy
        self._pending_seconds = pending_seconds
        self._dashboard_url = dashboard_url

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

    def _pending_run(
        self, conn, *, chat_id: str, workspace: WorkspacePolicy, prompt: str,
        parent_task_id: Optional[str] = None,
    ) -> str:
        action_id = self._pending(
            conn,
            chat_id=chat_id,
            action="run",
            payload={
                "parent_task_id": parent_task_id,
                "title": f"Telegram: {workspace.alias}",
                "prompt": prompt,
                "workspace": str(workspace.path),
                "verify_command": workspace.profile.command,
                "verify_timeout": workspace.profile.timeout,
            },
        )
        if parent_task_id:
            return (
                f"Pending follow-up {action_id} to {parent_task_id}.\n"
                f"Confirm with /confirm {action_id}"
            )
        return f"Pending intake {action_id}. Confirm with /confirm {action_id}"

    def _workspace_for_task(self, conn, task_id: str) -> Optional[WorkspacePolicy]:
        """A follow-up belongs in the same workspace as the task it revises."""
        if self._policy is None:
            return None
        recorded = board.task_workspace(conn, task_id)
        if not recorded:
            return None
        try:
            target = Path(recorded).resolve()
        except OSError:
            return None
        for workspace in self._policy.workspaces.values():
            try:
                if Path(workspace.path).resolve() == target:
                    return workspace
            except OSError:
                continue
        return None

    def _files(self, conn) -> str:
        """List what Tri-AI has produced, with links when a dashboard is known."""
        entries = board.recent_deliverables(conn, limit=20)
        if not entries:
            return "No files produced yet."
        lines = ["Files Tri-AI has produced:"]
        for entry in entries:
            prompt = " ".join(str(entry["prompt"] or "").split())[:70]
            lines.append("")
            lines.append(f"{prompt or entry['task_id']}")
            for index, item in enumerate(entry["paths"]):
                lines.append(f"  • {item['path']}")
                if self._dashboard_url:
                    base = self._dashboard_url.rstrip("/")
                    lines.append(f"    {base}/artifact/{entry['task_id']}/{index}")
        return "\n".join(lines)

    def _workspaces(self) -> str:
        if self._policy is None:
            return "Task intake is not configured. Read commands remain available."
        aliases = [
            f"{alias} (default)" if alias == self._policy.default_workspace else alias
            for alias in sorted(self._policy.workspaces)
        ]
        return "Workspaces: " + ", ".join(aliases) + ". Use /run <workspace-alias> <prompt>."

    def _help(self) -> str:
        commands = [
            "/status",
            "/task <task-id>",
            "/logs <task-id>",
            "/workspaces",
            "/files",
            "/run <workspace-alias> <prompt>",
            "/retry <task-id>",
            "/cancel <task-id>",
            "/confirm <request-id>",
        ]
        suffix = (
            " Plain text creates a confirmation-required draft in the default workspace."
            " Reply to a finished task's message to queue a follow-up linked to it."
        )
        if self._policy is None:
            suffix = " Task intake is not configured."
        elif self._policy.default_workspace is None:
            suffix = " Plain text needs default_workspace configured; use /workspaces."
        return "Commands: " + ", ".join(commands) + "." + suffix

    def dispatch(
        self,
        text: str,
        *,
        chat_id: str,
        board_path: Path | str,
        ledger_path: Path | str,
        runs_root: Path | str,
        reply_to_message_id: Optional[int] = None,
    ) -> str:
        """Return one honest command response; no command accepts shell text."""
        normalized = text.strip()
        if not normalized:
            return self._help()
        natural_read = NATURAL_READ_COMMANDS.get(normalized.casefold())
        if natural_read is not None:
            return telegram_read_surface.dispatch_command(
                natural_read, board_path=board_path, ledger_path=ledger_path, runs_root=runs_root
            )
        if not normalized.startswith("/"):
            if self._policy is None:
                return "Task intake is not configured. Use /status or /help."
            if self._policy.default_workspace is None:
                return "No default workspace is configured. Use /workspaces, then /run <workspace-alias> <prompt>."
            conn = board.connect(Path(board_path))
            try:
                # Replying to a completion card means "another go at that one".
                parent = None
                workspace = self._policy.workspaces[self._policy.default_workspace]
                if reply_to_message_id is not None:
                    parent = board.task_for_notified_message(
                        conn, chat_id=chat_id, message_id=reply_to_message_id,
                    )
                    if parent is not None:
                        scoped = self._workspace_for_task(conn, parent)
                        if scoped is not None:
                            workspace = scoped
                return self._pending_run(
                    conn, chat_id=chat_id, workspace=workspace,
                    prompt=normalized, parent_task_id=parent,
                )
            finally:
                conn.close()

        parts = normalized.split(maxsplit=2)
        command = parts[0].lower()
        if command == "/help":
            return self._help()
        if command == "/workspaces":
            return self._workspaces()
        if command == "/files":
            conn = board.connect(Path(board_path))
            try:
                return self._files(conn)
            finally:
                conn.close()
        if command in READ_COMMANDS:
            return telegram_read_surface.dispatch_command(
                normalized, board_path=board_path, ledger_path=ledger_path, runs_root=runs_root
            )
        conn = board.connect(Path(board_path))
        try:
            if command == "/run":
                if self._policy is None:
                    return "Task intake is not configured. Use /status or /help."
                if len(parts) != 3:
                    return "Usage: /run <workspace-alias> <prompt>"
                if parts[1] not in self._policy.workspaces:
                    return f"Unknown workspace alias: {parts[1]}. Use /workspaces."
                workspace = self._policy.workspaces[parts[1]]
                return self._pending_run(conn, chat_id=chat_id, workspace=workspace, prompt=parts[2])
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

    def pending_notifications(self, *, chat_id: str, board_path: Path | str) -> tuple[proposals.OutboundProposal, ...]:
        """Return unsent board proposals for one authorized transport recipient."""
        conn = board.connect(Path(board_path))
        try:
            return tuple(proposals.render(item) for item in board.pending_proposals_for_chat(conn, chat_id))
        finally:
            conn.close()

    def pending_completions(
        self, *, chat_id: str, board_path: Path | str, dashboard_url: Optional[str] = None,
    ) -> tuple[completion_report.CompletionCard, ...]:
        """Return finished runs this chat has not been told about yet."""
        conn = board.connect(Path(board_path))
        try:
            return tuple(
                completion_report.render(row, dashboard_url=dashboard_url)
                for row in board.pending_completions_for_chat(conn, chat_id)
            )
        finally:
            conn.close()

    def record_completion(
        self,
        *,
        task_id: str,
        run_id: int,
        chat_id: str,
        message_id: int,
        board_path: Path | str,
    ) -> bool:
        """Persist a delivered completion; an undelivered one stays retryable."""
        conn = board.connect(Path(board_path))
        try:
            return board.record_completion_notification(
                conn, task_id=task_id, run_id=run_id, chat_id=chat_id, message_id=message_id,
            )
        finally:
            conn.close()

    def record_notification(
        self,
        *,
        proposal_id: str,
        chat_id: str,
        message_id: int,
        board_path: Path | str,
    ) -> bool:
        """Persist a successful transport delivery; an unsent proposal remains retryable."""
        conn = board.connect(Path(board_path))
        try:
            return board.record_proposal_notification(
                conn, proposal_id=proposal_id, chat_id=chat_id, message_id=message_id,
            )
        finally:
            conn.close()

    def dispatch_callback(
        self,
        data: str,
        *,
        chat_id: str,
        board_path: Path | str,
        ledger_path: Path | str,
        runs_root: Path | str,
    ) -> CallbackResponse:
        """Apply a registered proposal decision; callback data cannot name a command."""
        del ledger_path, runs_root
        parts = data.split(":", 2)
        if len(parts) != 3 or parts[0] != "prop" or parts[1] not in {"approve", "reject"}:
            return CallbackResponse("Rejected unsafe callback payload.", True)
        proposal_id = parts[2]
        if not proposal_id or len(proposal_id) > 160 or any(char.isspace() for char in proposal_id):
            return CallbackResponse("Rejected unsafe callback payload.", True)
        if not chat_id:
            return CallbackResponse("Rejected unauthenticated callback.", True)
        conn = board.connect(Path(board_path))
        try:
            result = board.decide_proposal(
                conn, proposal_id=proposal_id, decision=parts[1],
            )
        finally:
            conn.close()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        if result.changed and result.status == "approved":
            return CallbackResponse(f"Approved by operator at {stamp}.", True)
        if result.changed and result.status == "rejected":
            return CallbackResponse(f"Rejected by operator at {stamp}.", True)
        if result.changed and result.status == "expired":
            return CallbackResponse(f"Expired safely at {stamp}: {result.detail}.", True)
        if result.status in {"approved", "rejected", "expired"}:
            return CallbackResponse(f"Already {result.status}: {result.detail}.", True)
        return CallbackResponse(f"Proposal was not applied: {result.status} ({result.detail}).", False)
