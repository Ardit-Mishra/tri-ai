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
import capability_catalog
import completion_report
import intake_preflight
import interpreter
import progress_card
import proposals
import telegram_read_surface
from memory import brain


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
ELLIPSIS = "…"
PLAY_ICON = "▶"
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
        catalog_path: Path | str = capability_catalog.DEFAULT_CATALOG_PATH,
        radar_path: Path | str = Path.home() / ".tri-ai" / "radar" / "latest.json",
        completer: Optional[interpreter.Completer] = None,
    ) -> None:
        if pending_seconds <= 0:
            raise ValueError("pending action lifetime must be positive")
        self._policy = policy
        self._pending_seconds = pending_seconds
        self._dashboard_url = dashboard_url
        self._catalog_path = Path(catalog_path)
        self._radar_path = Path(radar_path)
        # Absent by default, so the offline rules path is what runs unless a
        # caller deliberately wires a model in. Reading a message must never
        # be the reason the phone waits on a socket.
        self._completer = completer

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
        """Lead with talking, because that is now the whole interface.

        The old help opened with fourteen slash commands, which taught the
        operator that the bot was a command line with a chat window around it.
        They still all work and are listed below the fold; nothing was removed.
        """
        if self._policy is None:
            return "Task intake is not configured. I can still answer /status."
        lines = [
            "Just talk to me.",
            "",
            '  "build me a recipe card page for masala chai"  - I start, and '
            'tell you what I understood first.',
            '  "make it darker"  - refines what I just built.',
            '  "what is running?"  - the board, in words.',
            '  "stop"  - drops whatever is waiting.',
            "",
            "I only stop to ask when something sends, spends or publishes, "
            "or when a request is too thin to act on.",
            "",
            "Commands still work: /status, /task <id>, /logs <id>, /files, "
            "/workspaces, /run <workspace-alias> <prompt>, /retry <id>, "
            "/cancel <id>, /confirm <id>, /remember <note>, /recall <query>, "
            "/capabilities, /radar.",
        ]
        if self._policy.default_workspace is None:
            lines.append("Set default_workspace before plain text can start work.")
        return "\n".join(lines)


    def _capability_report(self) -> str:
        try:
            resources = capability_catalog.load_catalog(self._catalog_path)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return "Capability catalog is unavailable. Run the catalog refresh first."
        adapters = [item for item in resources if item.resource_id.startswith("adapter:")]
        ready_health = {
            "instruction-ready", "entrypoint-present", "path-present",
            "loopback-service-ready",
        }
        routable_availability = {"active", "executable", "registered"}
        ready = sum(
            item.availability in routable_availability and item.health_status in ready_health
            for item in adapters
        )
        staged = sum(item.availability == "source-only" for item in adapters)
        gated = sum(item.availability == "gated" for item in adapters)
        missing = sum(item.availability == "missing" for item in adapters)
        lines = [
            f"Tri-AI capabilities: {len(adapters)} adapters; {ready} routable, "
            f"{staged} source-only, {gated} gated, {missing} missing/radar candidates."
        ]
        for item in adapters:
            lines.append(
                f"- {item.name}: {item.availability} / {item.adapter_status} / {item.health_status}"
            )
        return "\n".join(lines)

    def _radar_report(self) -> str:
        try:
            document = json.loads(self._radar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "Technology radar report is unavailable."
        if document.get("schema") != "triai.technology-radar.v1":
            return "Technology radar report has an unsupported schema."
        candidates = document.get("candidates", [])
        evaluations = document.get("evaluations", [])
        errors = document.get("errors", [])
        lines = [
            f"Technology radar: {len(candidates)} candidates, {len(evaluations)} evaluated, "
            f"{len(errors)} source errors. Generated {document.get('generated_at', 'unknown')}."
        ]
        for evaluation in evaluations[:10]:
            candidate = evaluation.get("candidate", {}) if isinstance(evaluation, dict) else {}
            lines.append(
                f"- {candidate.get('name', 'unknown')}: "
                f"{evaluation.get('disposition', 'unknown')}"
            )
        return "\n".join(lines)

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
            conn = board.connect(Path(board_path))
            try:
                return self._converse(
                    conn, normalized, chat_id=chat_id, board_path=board_path,
                    ledger_path=ledger_path, runs_root=runs_root,
                    reply_to_message_id=reply_to_message_id,
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
        if command == "/capabilities":
            return self._capability_report()
        if command == "/radar":
            return self._radar_report()
        if command in READ_COMMANDS:
            return telegram_read_surface.dispatch_command(
                normalized, board_path=board_path, ledger_path=ledger_path, runs_root=runs_root
            )
        if command in {"/remember", "/recall"}:
            argument = normalized[len(parts[0]):].strip()
            if not argument:
                return f"Usage: {command} <{'note' if command == '/remember' else 'query'}>"
            brain_path = Path(board_path).resolve().parent / "brain" / "brain.db"
            memory = brain.connect(brain_path)
            try:
                if command == "/remember":
                    item = brain.capture(memory, argument, source=f"telegram:{chat_id}")
                    return (
                        f"Saved to Brain inbox as {item.item_id}. "
                        "Trust: UNREVIEWED until supported by verified evidence."
                    )
                pack = brain.context_pack(memory, argument, max_characters=3500)
                return pack.text or "No Brain memory matched that query."
            finally:
                memory.close()
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

    # ------------------------------------------------------------------
    # Conversation
    #
    # A message used to become a pending run and wait for /confirm, whatever
    # it said - so "hey" queued a task, and a half-specified request burned six
    # minutes producing the wrong thing. The interpreter now reads the message
    # first, and this decides what that reading is worth doing about.
    #
    # The gate is not removed, it is aimed. Send, spend and publish still stop
    # and ask, because those are the actions that cannot be taken back.
    # Everything else starts, and says what it understood while starting, so a
    # misreading costs a correction instead of a run.
    # ------------------------------------------------------------------

    MAX_TITLE = 70

    def _title_for(self, reading: "interpreter.Reading", fallback: str) -> str:
        """Name the task after the request.

        Every task used to be titled "Telegram: <workspace>", which left the
        board, the progress card and the completion card equally uninformative
        about what had actually been asked for.
        """
        title = (reading.understood or fallback).strip().rstrip(".")
        if len(title) > self.MAX_TITLE:
            title = title[: self.MAX_TITLE - 1].rstrip() + ELLIPSIS
        return title or "Untitled request"

    def _start_now(
        self, conn, *, chat_id: str, workspace: WorkspacePolicy,
        reading: "interpreter.Reading", prompt: str,
        parent_task_id: Optional[str] = None,
    ) -> str:
        """Create the request and confirm it in the same breath.

        It still goes through `create_pending_action` and
        `confirm_pending_action` rather than reaching into the task table, so
        there is exactly one path into board state and auto-started work is
        indistinguishable from confirmed work once it lands.
        """
        action_id = self._pending(
            conn,
            chat_id=chat_id,
            action="run",
            payload={
                "parent_task_id": parent_task_id,
                "title": self._title_for(reading, prompt),
                "prompt": reading.brief or prompt,
                "workspace": str(workspace.path),
                "verify_command": workspace.profile.command,
                "verify_timeout": workspace.profile.timeout,
            },
        )
        result = board.confirm_pending_action(
            conn, action_id=action_id, chat_id=chat_id)
        if not result.changed:
            return f"Could not start that: {result.status} ({result.detail})"

        verb = "Revising" if parent_task_id else "Starting"
        lines = [
            f"{PLAY_ICON} {verb}: {reading.understood}",
            f"task {result.task_id} in {workspace.alias}",
        ]
        if reading.source == "rules":
            # Say so. A word-list reading is weaker than a model's, and the
            # operator should know which one just decided what to build.
            lines.append("read without a model - correct me if that is wrong")
        flagged = intake_preflight.warning_line(
            intake_preflight.check(reading.brief or prompt, workspace.path),
            workspace.alias,
        )
        if flagged:
            lines.append(flagged)
        lines.append('Say "stop" to cancel, or just tell me what to change.')
        return "\n".join(lines)

    def _converse(
        self, conn, text: str, *, chat_id: str, board_path: Path | str,
        ledger_path: Path | str, runs_root: Path | str,
        reply_to_message_id: Optional[int] = None,
    ) -> str:
        """Read one message, then act on what it turned out to mean."""
        if self._policy is None:
            return "Task intake is not configured. Use /status or /help."
        if self._policy.default_workspace is None:
            return ("No default workspace is configured. Use /workspaces, then "
                    "/run <workspace-alias> <prompt>.")

        # Read against the history as it stood *before* this message, then
        # record - otherwise the message being read is already in its own
        # context and a follow-up looks like it refines itself.
        history = board.recent_turns(conn, chat_id=chat_id)
        board.record_turn(conn, chat_id=chat_id, role="operator", text=text)
        reading = interpreter.interpret(
            text, history=history, complete=self._completer)
        reply = self._act_on(
            conn, reading, text, chat_id=chat_id, board_path=board_path,
            ledger_path=ledger_path, runs_root=runs_root,
            reply_to_message_id=reply_to_message_id,
        )
        board.record_turn(conn, chat_id=chat_id, role="kaya", text=str(reply))
        return reply

    def _act_on(
        self, conn, reading: "interpreter.Reading", text: str, *, chat_id: str,
        board_path: Path | str, ledger_path: Path | str, runs_root: Path | str,
        reply_to_message_id: Optional[int] = None,
    ) -> str:
        if reading.intent == "chat":
            return ("I'm here. Tell me what to make and I'll start - no slash "
                    'commands needed. Ask "what is running?" any time.')
        if reading.intent == "stop":
            return self._stop(conn, chat_id=chat_id)
        if reading.intent == "ask":
            return telegram_read_surface.dispatch_command(
                "/status", board_path=board_path, ledger_path=ledger_path,
                runs_root=runs_root,
            )
        if reading.intent == "unclear" or reading.question:
            # One question, and nothing created. Guessing here is what spent
            # six minutes producing an artifact nobody asked for.
            head = (f"Got: {reading.understood}\n"
                    if reading.intent != "unclear" else "")
            return f"{head}{reading.question}"

        parent = None
        workspace = self._policy.workspaces[self._policy.default_workspace]
        if reply_to_message_id is not None:
            # Replying to a card means "another go at that one".
            parent = board.task_for_notified_message(
                conn, chat_id=chat_id, message_id=reply_to_message_id)
            if parent is not None:
                scoped = self._workspace_for_task(conn, parent)
                if scoped is not None:
                    workspace = scoped

        if reading.external:
            # The one interruption worth having. Nothing that sends, spends or
            # publishes starts on a reading alone.
            return self._pending_run(
                conn, chat_id=chat_id, workspace=workspace,
                prompt=reading.brief or text, parent_task_id=parent,
            )
        return self._start_now(
            conn, chat_id=chat_id, workspace=workspace, reading=reading,
            prompt=text, parent_task_id=parent,
        )

    def _stop(self, conn, *, chat_id: str) -> str:
        """Withdraw what is waiting; name what is already running.

        Stopping a running task still goes through `/cancel <task-id>`, because
        which one to stop is a choice only the operator can make and guessing
        at it would throw away work.
        """
        outstanding = board.pending_actions_for_chat(conn, chat_id)
        for item in outstanding:
            board.cancel_pending_action(
                conn, action_id=item["id"], chat_id=chat_id)
        if outstanding:
            return f"Cancelled {len(outstanding)} request(s) that were waiting."
        return ("Nothing was waiting. If something is already running, "
                "send /status to see it and /cancel <task-id> to stop it.")

    def pending_notifications(self, *, chat_id: str, board_path: Path | str) -> tuple[proposals.OutboundProposal, ...]:
        """Return unsent board proposals for one authorized transport recipient."""
        conn = board.connect(Path(board_path))
        try:
            return tuple(proposals.render(item) for item in board.pending_proposals_for_chat(conn, chat_id))
        finally:
            conn.close()

    def pending_completions(
        self, *, chat_id: str, board_path: Path | str,
        dashboard_url: Optional[str] = None,
        runs_root: Optional[Path | str] = None,
    ) -> tuple[completion_report.CompletionCard, ...]:
        """Return finished runs this chat has not been told about yet."""
        conn = board.connect(Path(board_path))
        try:
            return tuple(
                completion_report.render(row, dashboard_url=dashboard_url,
                                         runs_root=runs_root)
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
