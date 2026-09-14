"""Long-polling HTTPS transport for the read-only Telegram board adapter.

Only this module speaks to Telegram or reads its token. The read surface remains
a local renderer with no credential, network, subprocess, or board mutation
capability. A transport failure deliberately stops the daemon; it is never
retried invisibly.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence
from urllib import request

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import attachments
import telegram_read_surface
import telegram_control


TOKEN_ENV = "TRI_AI_TELEGRAM_BOT_TOKEN"
AUTHORIZED_CHAT_IDS_ENV = "TRI_AI_TELEGRAM_AUTHORIZED_CHAT_IDS"
AUTHORIZED_CHAT_ID_ENV = "TRI_AI_TELEGRAM_AUTHORIZED_CHAT_ID"
DEFAULT_CONFIG_PATH = Path.home() / ".tri-ai" / "config.json"
MAX_MESSAGE_CHARS = 4000


class TelegramTransportError(RuntimeError):
    """An HTTPS or Bot API failure that must stop the polling daemon."""


class TelegramApi(Protocol):
    """Minimal transport seam exercised by the daemon tests."""

    def get_updates(self, *, offset: Optional[int], timeout: int) -> list[dict[str, Any]]:
        """Return Telegram update objects."""

    def send_message(
        self, *, chat_id: str, text: str, reply_markup: Optional[dict[str, Any]] = None,
    ) -> Optional[int]:
        """Send one response or proposal card and return Telegram's message ID."""

    def edit_message(
        self, *, chat_id: str, message_id: int, text: str, reply_markup: dict[str, Any],
    ) -> None:
        """Replace a resolved proposal card and its active inline buttons."""


@dataclass(frozen=True)
class TelegramSettings:
    token: str = field(repr=False)
    authorized_chat_ids: frozenset[str]


def settings_from_environment(env: Mapping[str, str]) -> TelegramSettings:
    """Read only the daemon's explicit process-environment settings."""
    return _settings_from_values(env.get(TOKEN_ENV, ""), _authorized_values(env))


def _authorized_values(values: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        value.strip()
        for value in (
            values.get(AUTHORIZED_CHAT_ID_ENV, ""),
            *values.get(AUTHORIZED_CHAT_IDS_ENV, "").split(","),
        )
        if value.strip()
    )


def _settings_from_values(token: str, authorized_chat_ids: Sequence[str]) -> TelegramSettings:
    token = token.strip()
    chat_ids = frozenset(value.strip() for value in authorized_chat_ids if value.strip())
    missing: list[str] = []
    if not token:
        missing.append(TOKEN_ENV)
    if not chat_ids:
        missing.append(f"{AUTHORIZED_CHAT_ID_ENV} or {AUTHORIZED_CHAT_IDS_ENV}")
    if missing:
        raise ValueError(
            "Telegram daemon requires: " + "; ".join(missing)
        )
    return TelegramSettings(token=token, authorized_chat_ids=chat_ids)


def _config_values(path: Path) -> Mapping[str, str]:
    """Read the optional, user-profile configuration without exposing values."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Telegram configuration file is unreadable or invalid: {path}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("telegram", {}), dict):
        raise ValueError(f"Telegram configuration file has no object at telegram: {path}")
    telegram = raw.get("telegram", {})
    values: dict[str, str] = {}
    for key, environment_name in (
        ("bot_token", TOKEN_ENV),
        ("authorized_chat_id", AUTHORIZED_CHAT_ID_ENV),
        ("authorized_chat_ids", AUTHORIZED_CHAT_IDS_ENV),
    ):
        value = telegram.get(key)
        if value is None:
            continue
        if key == "authorized_chat_ids" and isinstance(value, list) and all(
            isinstance(item, str) for item in value
        ):
            values[environment_name] = ",".join(value)
        elif isinstance(value, str):
            values[environment_name] = value
        else:
            raise ValueError(f"Telegram configuration field {key} must be a string: {path}")
    return values


def _windows_user_environment() -> Mapping[str, str]:
    """Read only Tri-AI's named Windows user variables when process env lacks them."""
    if os.name != "nt":
        return {}
    try:
        import winreg

        values: dict[str, str] = {}
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            for name in (TOKEN_ENV, AUTHORIZED_CHAT_ID_ENV, AUTHORIZED_CHAT_IDS_ENV):
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    continue
                if isinstance(value, str):
                    values[name] = value
        return values
    except OSError:
        return {}


def settings_from_sources(
    env: Mapping[str, str],
    *,
    config_path: Optional[Path] = None,
    user_environment: Optional[Mapping[str, str]] = None,
) -> TelegramSettings:
    """Resolve settings from process env, local config, then Windows user env.

    Process environment wins so a deliberate per-session override is honored.
    Config and registry values are used only for missing fields. Values are kept
    in memory only and never included in diagnostics, argv, logs, or state.
    """
    environment_token = env.get(TOKEN_ENV, "").strip()
    environment_ids = _authorized_values(env)
    if environment_token and environment_ids:
        return _settings_from_values(environment_token, environment_ids)

    configured = _config_values(config_path or DEFAULT_CONFIG_PATH)
    registry = _windows_user_environment() if user_environment is None else user_environment
    token = environment_token or configured.get(TOKEN_ENV, "").strip() or registry.get(TOKEN_ENV, "").strip()
    authorized = (
        environment_ids
        or _authorized_values(configured)
        or _authorized_values(registry)
    )
    return _settings_from_values(token, authorized)


def startup_diagnostic(exc: Exception) -> str:
    """Name the failure class and safe reason without printing configuration values."""
    return f"telegram daemon stopped: {type(exc).__name__}: {exc}"


class HttpsTelegramApi:
    """Tiny HTTPS-only Telegram Bot API client; no webhook listener is opened."""

    def __init__(
        self,
        token: str,
        *,
        opener: Callable[..., Any] = request.urlopen,
    ) -> None:
        self._token = token
        self._opener = opener

    def _call(self, method: str, payload: dict[str, Any], *, timeout: int) -> Any:
        data = json.dumps(payload).encode("utf-8")
        endpoint = f"https://api.telegram.org/bot{self._token}/{method}"
        req = request.Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with self._opener(req, timeout=timeout) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            raise TelegramTransportError(
                f"Telegram HTTPS request failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(decoded, dict) or decoded.get("ok") is not True:
            raise TelegramTransportError("Telegram Bot API rejected the request")
        return decoded.get("result")

    def download_file(
        self,
        file_id: str,
        destination: Path,
        *,
        timeout: int = 120,
        max_bytes: int = attachments.MAX_ATTACHMENT_BYTES,
    ) -> int:
        """Fetch one file by id into ``destination``. Returns bytes written.

        Two calls, as the Bot API requires: ``getFile`` resolves an id to a
        temporary path, then the file is read from the download host - a
        different host to the API one, and the only place the token appears in a
        URL rather than a header.

        The read is chunked and capped. ``getFile`` reports a size but the
        caller does not have to believe it: a body that keeps coming past
        ``max_bytes`` is abandoned and the partial file removed, so a wrong or
        absent Content-Length cannot fill the disk.
        """
        described = self._call("getFile", {"file_id": file_id}, timeout=timeout)
        if not isinstance(described, dict):
            raise TelegramTransportError("getFile returned no file description")
        remote = described.get("file_path")
        if not isinstance(remote, str) or not remote:
            raise TelegramTransportError("getFile returned no file_path")

        url = f"https://api.telegram.org/file/bot{self._token}/{remote}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with self._opener(request.Request(url), timeout=timeout) as response:
                with destination.open("wb") as sink:
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > max_bytes:
                            raise TelegramTransportError(
                                f"attachment exceeds {max_bytes} bytes"
                            )
                        sink.write(chunk)
        except TelegramTransportError:
            destination.unlink(missing_ok=True)
            raise
        except (OSError, ValueError) as exc:
            destination.unlink(missing_ok=True)
            raise TelegramTransportError(
                f"attachment download failed: {type(exc).__name__}"
            ) from exc
        return written

    def _call_raw(self, method: str, body: bytes, *, content_type: str, timeout: int) -> Any:
        """Post an already-encoded body; used for multipart document uploads."""
        req = request.Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        try:
            with self._opener(req, timeout=timeout) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            raise TelegramTransportError(
                f"Telegram HTTPS request failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(decoded, dict) or decoded.get("ok") is not True:
            raise TelegramTransportError("Telegram Bot API rejected the request")
        return decoded.get("result")

    def get_updates(self, *, offset: Optional[int], timeout: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload, timeout=timeout + 10)
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise TelegramTransportError("Telegram getUpdates returned an invalid result")
        return result

    def send_message(
        self, *, chat_id: str, text: str, reply_markup: Optional[dict[str, Any]] = None,
    ) -> Optional[int]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = self._call("sendMessage", payload, timeout=20)
        if not isinstance(result, dict) or isinstance(result.get("message_id"), bool) or not isinstance(result.get("message_id"), int):
            raise TelegramTransportError("Telegram sendMessage returned no message ID")
        return int(result["message_id"])

    def send_document(
        self, *, chat_id: str, filename: str, content: bytes, caption: str = "",
    ) -> Optional[int]:
        """Upload one file as a Telegram document via multipart/form-data.

        A link needs a reachable host; the file itself does not. This is the
        path that still works when the operator is off the tailnet.
        """
        boundary = "----triai" + secrets.token_hex(16)
        fields = [("chat_id", chat_id)]
        if caption:
            fields.append(("caption", caption[:1000]))
        body = bytearray()
        for name, value in fields:
            body += f"--{boundary}\r\n".encode("utf-8")
            body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
            body += value.encode("utf-8") + b"\r\n"
        body += f"--{boundary}\r\n".encode("utf-8")
        body += (
            f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        body += content + b"\r\n"
        body += f"--{boundary}--\r\n".encode("utf-8")
        result = self._call_raw(
            "sendDocument",
            bytes(body),
            content_type=f"multipart/form-data; boundary={boundary}",
            timeout=60,
        )
        if not isinstance(result, dict):
            return None
        message_id = result.get("message_id")
        if isinstance(message_id, bool) or not isinstance(message_id, int):
            return None
        return int(message_id)

    def edit_message(
        self, *, chat_id: str, message_id: int, text: str, reply_markup: dict[str, Any],
    ) -> None:
        self._call(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": text, "reply_markup": reply_markup},
            timeout=20,
        )


def _message_chunks(text: str, *, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Preserve all adapter output when it exceeds Telegram's message limit."""
    if limit <= 0:
        raise ValueError("message chunk limit must be positive")
    return [text[index:index + limit] for index in range(0, len(text), limit)] or [""]


def _held_notice(stored: Sequence[attachments.StoredAttachment]) -> str:
    """Acknowledge files sent with no instruction, and say what happens next.

    Guessing what an unexplained file is for would be worse than waiting: the
    same picture could be a logo to use, a design to copy, or a screenshot of a
    bug. So it is held, and the operator is told plainly that it is held.
    """
    if not stored:
        return "Nothing could be saved from that message."
    noun = "file" if len(stored) == 1 else "files"
    lines = [f"Got {len(stored)} {noun}:"]
    for item in stored:
        lines.append(f"  • {item.original}  ({item.size:,} bytes)")
    lines.append("")
    lines.append("Held for your next request — tell me what to build with them.")
    return "\n".join(lines)


def _normalized_command(text: str) -> str:
    """Accept a command addressed as slash-command at a bot name."""
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return text
    command = parts[0].split("@", 1)[0]
    return " ".join((command, *parts[1:]))


class TelegramDaemon:
    """Poll, authorize, then pass text commands into the local read surface."""

    def __init__(
        self,
        api: TelegramApi,
        settings: TelegramSettings,
        *,
        board_path: Path | str,
        ledger_path: Path | str,
        runs_root: Path | str,
        renderer: Callable[..., str] = telegram_read_surface.dispatch_command,
        handler: Optional[Callable[..., str]] = None,
        callback_handler: Optional[Callable[..., Any]] = None,
        notifier: Optional[Callable[..., Sequence[Any]]] = None,
        notification_recorder: Optional[Callable[..., bool]] = None,
        completion_notifier: Optional[Callable[..., Sequence[Any]]] = None,
        completion_recorder: Optional[Callable[..., bool]] = None,
        dashboard_url: Optional[str] = None,
        progress_opener: Optional[Callable[..., Sequence[Any]]] = None,
        progress_notifier: Optional[Callable[..., Sequence[Any]]] = None,
        progress_starter: Optional[Callable[..., bool]] = None,
        progress_advancer: Optional[Callable[..., bool]] = None,
    ) -> None:
        self._api = api
        self._settings = settings
        self._board_path = board_path
        self._ledger_path = ledger_path
        self._runs_root = runs_root
        self._renderer = renderer
        self._handler = handler
        self._callback_handler = callback_handler
        self._notifier = notifier
        self._notification_recorder = notification_recorder
        self._completion_notifier = completion_notifier
        self._completion_recorder = completion_recorder
        self._dashboard_url = dashboard_url
        self._progress_opener = progress_opener
        self._progress_notifier = progress_notifier
        self._progress_starter = progress_starter
        self._progress_advancer = progress_advancer

    def publish_pending(self) -> None:
        """Push each pending card once per authorized chat through an injected control seam."""
        if self._notifier is None or self._notification_recorder is None:
            return
        for chat_id in sorted(self._settings.authorized_chat_ids):
            for card in self._notifier(chat_id=chat_id, board_path=self._board_path):
                message_id = self._api.send_message(
                    chat_id=chat_id, text=card.text, reply_markup=card.reply_markup,
                )
                if message_id is None:
                    raise TelegramTransportError("proposal notification did not return a message ID")
                self._notification_recorder(
                    proposal_id=card.proposal_id, chat_id=chat_id,
                    message_id=message_id, board_path=self._board_path,
                )

    def publish_completions(self) -> None:
        """Push each finished run to each authorized chat, at-least-once.

        Send first, record the receipt second. A send that fails leaves the run
        unrecorded and is retried next poll; a crash *between* the two re-sends
        a notice the operator already saw. Duplicating a completion notice is
        the better of the two failures - recording first would lose the notice
        entirely when the send fails, and a finished task nobody hears about is
        exactly the silence this whole path exists to end.
        """
        if self._completion_notifier is None or self._completion_recorder is None:
            return
        for chat_id in sorted(self._settings.authorized_chat_ids):
            for card in self._completion_notifier(
                chat_id=chat_id,
                board_path=self._board_path,
                dashboard_url=self._dashboard_url,
            ):
                message_id: Optional[int] = None
                chunks = _message_chunks(card.text)
                for index, chunk in enumerate(chunks):
                    sent = self._api.send_message(
                        chat_id=chat_id,
                        text=chunk,
                        reply_markup=(
                            telegram_control.completion_keyboard(card.task_id)
                            if index == len(chunks) - 1 else None
                        ),
                    )
                    if message_id is None:
                        message_id = sent
                if message_id is None:
                    raise TelegramTransportError("completion notice did not return a message ID")
                self._deliver_documents(chat_id, card)
                self._completion_recorder(
                    task_id=card.task_id, run_id=card.run_id, chat_id=chat_id,
                    message_id=message_id, board_path=self._board_path,
                )

    def _deliver_documents(self, chat_id: str, card: Any) -> None:
        """Send small text artifacts as files as well as links.

        A link needs the operator to be on the tailnet; the file does not. Only
        self-contained text types travel this way, and only under the size cap -
        anything larger stays a link rather than a slow upload.
        """
        if not getattr(self._api, "send_document", None):
            return
        for artifact in getattr(card, "documents", ()) or ():
            path = Path(artifact["absolute"])
            try:
                content = path.read_bytes()
            except OSError:
                continue
            try:
                self._api.send_document(
                    chat_id=chat_id, filename=path.name, content=content,
                    caption=artifact.get("caption", ""),
                )
            except TelegramTransportError:
                # The card and its link already landed; a failed upload is not
                # worth losing the delivery record over.
                continue

    def poll_once(self, *, offset: Optional[int], timeout: int) -> Optional[int]:
        """Process one long-poll response and return the next Telegram offset."""
        updates = self._api.get_updates(offset=offset, timeout=timeout)
        next_offset = offset
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                next_offset = max(next_offset or update_id + 1, update_id + 1)
            callback = update.get("callback_query")
            if isinstance(callback, dict):
                self._handle_callback(callback)
                continue
            message = update.get("message")
            if not isinstance(message, dict):
                continue
            chat = message.get("chat")
            if not isinstance(chat, dict):
                continue
            chat_id = str(chat.get("id", ""))
            if chat_id not in self._settings.authorized_chat_ids:
                continue

            # A message carrying a file puts its words in `caption`, not `text`.
            # Requiring `text` meant every PDF, photo and document was dropped
            # here in silence - the operator saw nothing happen at all.
            offered = attachments.from_message(message)
            text = message.get("text")
            if not isinstance(text, str):
                caption = message.get("caption")
                text = caption if isinstance(caption, str) else ""
            if not text and not offered:
                continue

            stored: tuple[attachments.StoredAttachment, ...] = ()
            if offered:
                stored, rejected = self._store_attachments(chat_id, offered)
                for note in rejected:
                    self._api.send_message(chat_id=chat_id, text=note)
                if not text:
                    # Files with no words. Hold them for the next instruction
                    # rather than guessing what they are for.
                    self._api.send_message(
                        chat_id=chat_id, text=_held_notice(stored),
                    )
                    continue

            # Anything sent earlier without words belongs to this instruction.
            stored = self._claim_pending(chat_id) + stored
            command = _normalized_command(text)
            if stored and not command.startswith("/"):
                # Only a natural-language request becomes a task prompt; a slash
                # command is a board query and has nothing to attach files to.
                command = command + attachments.describe_for_prompt(stored)
            replied = message.get("reply_to_message")
            reply_to_message_id = None
            if isinstance(replied, dict):
                candidate = replied.get("message_id")
                if isinstance(candidate, int) and not isinstance(candidate, bool):
                    reply_to_message_id = candidate
            if self._handler is not None:
                rendered = self._handler(
                    command,
                    chat_id=chat_id,
                    board_path=self._board_path,
                    ledger_path=self._ledger_path,
                    runs_root=self._runs_root,
                    reply_to_message_id=reply_to_message_id,
                )
            else:
                rendered = self._renderer(
                    command,
                    board_path=self._board_path,
                    ledger_path=self._ledger_path,
                    runs_root=self._runs_root,
                )
            # A staged reply may carry inline buttons instead of an id to retype.
            markup = getattr(rendered, "reply_markup", None)
            chunks = _message_chunks(getattr(rendered, "text", rendered))
            for index, chunk in enumerate(chunks):
                # Buttons ride the last chunk so they sit under the whole reply.
                self._api.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    reply_markup=markup if index == len(chunks) - 1 else None,
                )
        self.publish_pending()
        self.publish_progress()
        self.publish_completions()
        return next_offset

    def publish_progress(self) -> None:
        """Open a card for newly running tasks, and edit existing ones in place.

        One message per task per chat, and nothing is sent when no phase
        changed, so an idle poll costs no Telegram calls. Like completions this
        is at-least-once: a crash between sending a card and recording it opens
        a second card for the same task on the next poll.
        """
        if self._progress_opener is None or self._progress_advancer is None:
            return
        for chat_id in sorted(self._settings.authorized_chat_ids):
            for card in self._progress_opener(
                chat_id=chat_id, board_path=self._board_path,
            ):
                message_id = self._api.send_message(chat_id=chat_id, text=card.text)
                if message_id is None:
                    continue
                self._progress_starter(
                    task_id=card.task_id, chat_id=chat_id, message_id=message_id,
                    phase=card.phase, board_path=self._board_path,
                )
            for message_id, card in self._progress_notifier(
                chat_id=chat_id, board_path=self._board_path,
            ):
                try:
                    self._api.edit_message(
                        chat_id=chat_id, message_id=message_id,
                        text=card.text, reply_markup={"inline_keyboard": []},
                    )
                except TelegramTransportError:
                    # An edit that fails leaves the phase unrecorded, so the next
                    # poll retries rather than losing the transition.
                    continue
                self._progress_advancer(
                    task_id=card.task_id, chat_id=chat_id, phase=card.phase,
                    closed=card.closed, board_path=self._board_path,
                )

    def _attachment_dir(self, chat_id: str, *, pending: bool) -> Path:
        """Per-chat storage, outside every workspace. See src/attachments.py."""
        # `runs_root` arrives as a str from the CLI and as a Path from tests;
        # coerce rather than assume, since guessing wrong here would put
        # attachments somewhere nobody looks.
        root = attachments.attachments_root(Path(self._runs_root).parent)
        safe_chat = attachments.safe_name(chat_id, fallback="chat")
        return root / safe_chat / ("pending" if pending else "claimed")

    def _store_attachments(
        self, chat_id: str, offered: Sequence[attachments.Attachment],
    ) -> tuple[tuple[attachments.StoredAttachment, ...], tuple[str, ...]]:
        """Download what was offered. Returns what landed, and what to say about
        what did not.

        A download that fails is reported to the operator rather than swallowed:
        a file they sent and never heard about again is the same silence this
        whole feature exists to remove.
        """
        directory = self._attachment_dir(chat_id, pending=True)
        landed: list[attachments.StoredAttachment] = []
        problems: list[str] = []
        for item in offered:
            if item.too_large:
                problems.append(
                    f"{item.name} is {item.size:,} bytes — over the "
                    f"{attachments.MAX_ATTACHMENT_BYTES:,} byte limit the "
                    f"Telegram Bot API will serve, so it could not be fetched."
                )
                continue
            try:
                target = attachments.unique_path(directory, item.name)
                size = self._api.download_file(item.file_id, target)
            except (TelegramTransportError, ValueError, OSError) as exc:
                problems.append(
                    f"{item.name} could not be saved: {type(exc).__name__}."
                )
                continue
            landed.append(attachments.StoredAttachment(
                path=target, kind=item.kind, size=size,
                original=item.name, mime=item.mime,
            ))
        return tuple(landed), tuple(problems)

    def _claim_pending(
        self, chat_id: str,
    ) -> tuple[attachments.StoredAttachment, ...]:
        """Take everything waiting for an instruction, so it attaches once only.

        Files are moved out of `pending/` as they are claimed; leaving them
        would silently re-attach them to every later request.
        """
        pending = self._attachment_dir(chat_id, pending=True)
        if not pending.is_dir():
            return ()
        claimed_dir = self._attachment_dir(chat_id, pending=False)
        out: list[attachments.StoredAttachment] = []
        for path in sorted(pending.iterdir()):
            if not path.is_file():
                continue
            try:
                target = attachments.unique_path(claimed_dir, path.name)
                size = path.stat().st_size
                path.replace(target)
            except (OSError, ValueError):
                continue
            out.append(attachments.StoredAttachment(
                path=target, kind="file", size=size, original=path.name,
            ))
        return tuple(out)

    def _handle_callback(self, callback: Mapping[str, Any]) -> None:
        """Authorize an inline decision before it reaches the local control surface."""
        if self._callback_handler is None:
            return
        message, actor, data = callback.get("message"), callback.get("from"), callback.get("data")
        if not isinstance(message, dict) or not isinstance(actor, dict) or not isinstance(data, str):
            return
        chat = message.get("chat")
        message_id = message.get("message_id")
        chat_id, actor_id = (
            str(chat.get("id", "")) if isinstance(chat, dict) else "",
            str(actor.get("id", "")),
        )
        if (
            chat_id not in self._settings.authorized_chat_ids
            or actor_id not in self._settings.authorized_chat_ids
            or isinstance(message_id, bool)
            or not isinstance(message_id, int)
        ):
            return
        response = self._callback_handler(
            data, chat_id=actor_id, board_path=self._board_path,
            ledger_path=self._ledger_path, runs_root=self._runs_root,
        )
        if response.remove_buttons:
            self._api.edit_message(
                chat_id=chat_id, message_id=message_id, text=response.text,
                reply_markup={"inline_keyboard": []},
            )
        else:
            self._api.send_message(chat_id=chat_id, text=response.text)

    def run_forever(
        self,
        *,
        poll_timeout: int,
        max_transport_failures: int = 10,
        backoff_base_seconds: float = 2.0,
        backoff_max_seconds: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Long-poll until an operator stop or a persistent transport failure.

        A single lost HTTPS request used to end this process. That is the wrong
        severity for a daemon on a home connection: a dropped packet is not a
        broken bot, and on 2026-09-12 one such exit took the whole fleet down
        with it, because the supervisor stopped the worker when any child
        exited. So a transport failure is now retried with exponential backoff.

        A *persistent* failure is different in kind - a revoked token or a
        rejected request fails identically forever - so the retry is bounded
        and the daemon still exits non-zero once the budget is spent, leaving
        the restart decision to the supervisor rather than spinning silently.
        Any successful poll clears the streak.
        """
        offset: Optional[int] = None
        failures = 0
        while True:
            try:
                offset = self.poll_once(offset=offset, timeout=poll_timeout)
            except TelegramTransportError as exc:
                failures += 1
                if failures >= max_transport_failures:
                    raise
                delay = min(backoff_base_seconds * (2 ** (failures - 1)), backoff_max_seconds)
                print(
                    f"telegram transport failure {failures}/{max_transport_failures}: "
                    f"{type(exc).__name__}: {exc}; retrying in {delay:.0f}s",
                    file=sys.stderr,
                    flush=True,
                )
                sleep(delay)
                continue
            failures = 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the read-only Tri-AI Telegram long-poll daemon.")
    parser.add_argument("--board", required=True, help="Tri-AI board SQLite path")
    parser.add_argument("--ledger", required=True, help="Tri-AI ledger JSONL path")
    parser.add_argument("--runs-dir", required=True, help="Root containing retained task logs")
    parser.add_argument(
        "--intake-policy",
        help="operator-owned JSON policy; without it Telegram remains read-only",
    )
    parser.add_argument("--poll-timeout", type=int, default=30, choices=range(1, 51))
    parser.add_argument(
        "--dashboard-url",
        help="Base URL of the read-only dashboard, used to link produced artifacts",
    )
    parser.add_argument("--once", action="store_true", help="Process one long-poll response, then exit")
    args = parser.parse_args(argv)

    try:
        settings = settings_from_sources(os.environ)
        transport = HttpsTelegramApi(settings.token)
        policy = telegram_control.load_policy(args.intake_policy) if args.intake_policy else None
        control = telegram_control.TelegramControl(policy, dashboard_url=args.dashboard_url)
        handler = control.dispatch
        callback_handler = control.dispatch_callback
        notifier = control.pending_notifications
        notification_recorder = control.record_notification
        daemon = TelegramDaemon(
            transport,
            settings,
            board_path=args.board,
            ledger_path=args.ledger,
            runs_root=args.runs_dir,
            handler=handler,
            callback_handler=callback_handler,
            notifier=notifier,
            notification_recorder=notification_recorder,
            completion_notifier=control.pending_completions,
            completion_recorder=control.record_completion,
            dashboard_url=args.dashboard_url,
            progress_opener=control.open_progress,
            progress_notifier=control.pending_progress,
            progress_starter=control.start_progress,
            progress_advancer=control.record_progress,
        )
        if args.once:
            daemon.poll_once(offset=None, timeout=args.poll_timeout)
        else:
            daemon.run_forever(poll_timeout=args.poll_timeout)
    except (TelegramTransportError, ValueError) as exc:
        print(startup_diagnostic(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
