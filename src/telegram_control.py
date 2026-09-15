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
from typing import Any, Mapping, Optional, Sequence

import board
import completion_report
import intake_preflight
import progress_card
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


class StagedReply(str):
    """A reply that also carries inline buttons.

    It subclasses str so every caller that treats a reply as text - the
    chunker, the tests, a transport with no button support - keeps working.
    The buttons are additive and the request id stays in the text, so tapping
    is the convenience and typing remains the fallback.
    """

    reply_markup: Optional[dict[str, Any]]

    def __new__(cls, text: str, reply_markup: Optional[dict[str, Any]] = None) -> "StagedReply":
        reply = super().__new__(cls, text)
        reply.reply_markup = reply_markup
        return reply

    @property
    def text(self) -> str:
        return str(self)


def confirm_keyboard(action_id: str) -> dict[str, Any]:
    """Approve or withdraw one staged request without typing its id."""
    return {"inline_keyboard": [[
        {"text": "\u2705 Confirm Task", "callback_data": f"confirm:{action_id}"},
        {"text": "\u274c Cancel", "callback_data": f"cancel:{action_id}"},
    ]]}


# Named rather than written inline. An escape inside an f-string's expression
# is a syntax error before Python 3.12, and `tests/run.ps1` runs the suite under
# the Hermes venv's 3.11 - so one inline "\U0001f4c1" made this module, and
# every test that imports it, unloadable. The whole Telegram surface sat
# untested that way: eight test files reported as loader errors.
OTHER_WORKSPACE_ICON = "\U0001f4c1"


def workspace_keyboard(aliases: Sequence[str]) -> dict[str, Any]:
    """Offer the configured workspaces rather than asking for one by name."""
    icons = {"sandbox": "\U0001f9ea", "genclarus": "\U0001f9ec", "tri-ai": "\u26a1"}
    row = [
        {"text": f"{icons.get(alias, OTHER_WORKSPACE_ICON)} {alias}",
         "callback_data": f"ws:{alias}"}
        for alias in aliases
    ]
    return {"inline_keyboard": [row[index:index + 3] for index in range(0, len(row), 3)]}


def completion_keyboard(task_id: str) -> dict[str, Any]:
    """Revise the artifact or read the gate's own output, from the card."""
    return {"inline_keyboard": [[
        {"text": "\U0001f501 Revise Artifact", "callback_data": f"revise:{task_id}"},
        {"text": "\U0001f4cb View Verify Log", "callback_data": f"log:{task_id}"},
    ]]}


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
        head = (
            f"Pending follow-up {action_id} to {parent_task_id}"
            if parent_task_id
            else f"Pending intake {action_id}"
        )
        # Naming the workspace, and flagging a prompt that points at a file the
        # workspace does not hold, is the difference between noticing a
        # mis-aimed task now and discovering it after the run has finished.
        flagged = intake_preflight.warning_line(
            intake_preflight.check(prompt, workspace.path), workspace.alias,
        )
        body = (
            f"{head}\n"
            f"workspace: {workspace.alias}\n"
            f"prompt: {prompt[:300]}\n"
            + (f"\n{flagged}\n" if flagged else "")
            + f"\nTap Confirm below, or send /confirm {action_id}"
        )
        return StagedReply(body, confirm_keyboard(action_id))

    def pending_progress(self, *, chat_id: str, board_path: Path | str) -> tuple[Any, ...]:
        """Cards whose phase has moved since they were last drawn.

        Returns one entry per card that actually changed, so an idle poll costs
        no edits. Each carries the message to edit and the new phase to record.
        """
        conn = board.connect(Path(board_path))
        try:
            now = int(time.time())
            moved = []
            for row in board.open_progress_cards(conn, chat_id):
                phase = progress_card.phase_for(row)
                if phase == row["phase"]:
                    continue
                moved.append((row["message_id"], progress_card.render(row, phase=phase, now=now)))
            return tuple(moved)
        finally:
            conn.close()

    def record_progress(
        self, *, task_id: str, chat_id: str, phase: str, closed: bool,
        board_path: Path | str,
    ) -> bool:
        conn = board.connect(Path(board_path))
        try:
            return board.advance_progress_card(
                conn, task_id=task_id, chat_id=chat_id, phase=phase, closed=closed,
            )
        finally:
            conn.close()

    def open_progress(self, *, chat_id: str, board_path: Path | str) -> tuple[Any, ...]:
        """Tasks that have started but have no card yet."""
        conn = board.connect(Path(board_path))
        try:
            now = int(time.time())
            tracked = {row["task_id"] for row in board.open_progress_cards(conn, chat_id)}
            fresh = []
            for row in board.running_tasks_without_cards(conn, chat_id):
                if row["task_id"] in tracked:
                    continue
                phase = progress_card.phase_for(row)
                fresh.append(progress_card.render(row, phase=phase, now=now))
            return tuple(fresh)
        finally:
            conn.close()

    def start_progress(
        self, *, task_id: str, chat_id: str, message_id: int, phase: str,
        board_path: Path | str,
    ) -> bool:
        conn = board.connect(Path(board_path))
        try:
            return board.record_progress_card(
                conn, task_id=task_id, chat_id=chat_id,
                message_id=message_id, phase=phase,
            )
        finally:
            conn.close()

    def _apply_confirm(self, conn, *, chat_id: str, action_id: str) -> str:
        """One place where a confirmation is applied, typed or tapped."""
        result = board.confirm_pending_action(conn, action_id=action_id, chat_id=chat_id)
        if result.changed:
            return f"Confirmed {action_id}: task {result.task_id} is {result.status}"
        return f"Confirmation refused: {result.status} ({result.detail})"

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
                if len(parts) == 1:
                    return StagedReply(
                        "Pick a workspace, then send your prompt as plain text.",
                        workspace_keyboard(sorted(self._policy.workspaces)),
                    )
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
            if command == "/confirm" and len(parts) == 1:
                outstanding = board.pending_actions_for_chat(conn, chat_id)
                if not outstanding:
                    return "Nothing is waiting for confirmation."
                if len(outstanding) > 1:
                    listed = "\n".join(
                        f"  {item['id']} — {str(item['payload'].get('prompt', ''))[:60]}"
                        for item in outstanding
                    )
                    return (
                        f"{len(outstanding)} requests are waiting; name one:\n{listed}"
                    )
                return self._apply_confirm(conn, chat_id=chat_id, action_id=outstanding[0]["id"])
            if command == "/confirm":
                if len(parts) != 2:
                    return "Usage: /confirm <request-id>"
                return self._apply_confirm(conn, chat_id=chat_id, action_id=parts[1])
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
        parts = data.split(":", 2)
        if not chat_id:
            return CallbackResponse("Rejected unauthenticated callback.", True)

        def safe(value: str) -> bool:
            return bool(value) and len(value) <= 160 and not any(c.isspace() for c in value)

        # Two-part verbs act on a staged request or a finished task. The verb
        # set is closed; callback data can never name a command.
        if len(parts) == 2 and parts[0] in {"confirm", "cancel", "ws", "revise", "log"}:
            verb, subject = parts
            if not safe(subject):
                return CallbackResponse("Rejected unsafe callback payload.", True)
            conn = board.connect(Path(board_path))
            try:
                if verb == "confirm":
                    return CallbackResponse(
                        self._apply_confirm(conn, chat_id=chat_id, action_id=subject), True,
                    )
                if verb == "cancel":
                    outcome = board.cancel_pending_action(
                        conn, action_id=subject, chat_id=chat_id,
                    )
                    if outcome.changed:
                        return CallbackResponse("Cancelled. Nothing was created.", True)
                    return CallbackResponse(
                        f"Cancel refused: {outcome.status} ({outcome.detail})", True,
                    )
                if verb == "ws":
                    if self._policy is None or subject not in self._policy.workspaces:
                        return CallbackResponse("Unknown workspace.", True)
                    return CallbackResponse(
                        f"Workspace {subject} selected. Send the prompt with "
                        f"/run {subject} <prompt>.",
                        True,
                    )
                if verb in {"revise", "log"}:
                    # These buttons live on a completion card, so the chat must
                    # be one this task was actually delivered to. Neither verb
                    # mutates, and an authorized chat can already type
                    # `/logs <task>` for any task - but a button that acts on a
                    # task the chat was never told about is a discrepancy
                    # between what the code enforces and what it claims, and the
                    # cheaper of the two fixes is to enforce it.
                    if not board.chat_was_notified(conn, chat_id=chat_id, task_id=subject):
                        return CallbackResponse(
                            "That task was not delivered to this chat.", True,
                        )
                    if verb == "revise":
                        return CallbackResponse(
                            f"Reply to this message with the change you want, and it "
                            f"will be queued as a follow-up to {subject}.",
                            False,
                        )
                    return CallbackResponse(
                        telegram_read_surface.dispatch_command(
                            f"/logs {subject}", board_path=board_path,
                            ledger_path=ledger_path, runs_root=runs_root,
                        ),
                        False,
                    )
            finally:
                conn.close()

        if len(parts) != 3 or parts[0] != "prop" or parts[1] not in {"approve", "reject"}:
            return CallbackResponse("Rejected unsafe callback payload.", True)
        proposal_id = parts[2]
        if not safe(proposal_id):
            return CallbackResponse("Rejected unsafe callback payload.", True)
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
