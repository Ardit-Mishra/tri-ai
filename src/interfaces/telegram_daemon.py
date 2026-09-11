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
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence
from urllib import request

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import telegram_read_surface
import telegram_control


TOKEN_ENV = "TRI_AI_TELEGRAM_BOT_TOKEN"
AUTHORIZED_CHAT_IDS_ENV = "TRI_AI_TELEGRAM_AUTHORIZED_CHAT_IDS"
AUTHORIZED_CHAT_ID_ENV = "TRI_AI_TELEGRAM_AUTHORIZED_CHAT_ID"
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
    """Read only the daemon's explicit credential and authorization settings."""
    token = env.get(TOKEN_ENV, "").strip()
    if not token:
        raise ValueError(f"{TOKEN_ENV} must be set")

    raw_ids = [env.get(AUTHORIZED_CHAT_ID_ENV, "")]
    raw_ids.extend(env.get(AUTHORIZED_CHAT_IDS_ENV, "").split(","))
    chat_ids = frozenset(value.strip() for value in raw_ids if value.strip())
    if not chat_ids:
        raise ValueError(
            f"set {AUTHORIZED_CHAT_ID_ENV} or {AUTHORIZED_CHAT_IDS_ENV} to at least one chat ID"
        )
    return TelegramSettings(token=token, authorized_chat_ids=chat_ids)


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
            raise TelegramTransportError("Telegram HTTPS request failed") from exc
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
            text = message.get("text")
            if not isinstance(chat, dict) or not isinstance(text, str):
                continue
            chat_id = str(chat.get("id", ""))
            if chat_id not in self._settings.authorized_chat_ids:
                continue
            command = _normalized_command(text)
            if self._handler is not None:
                rendered = self._handler(
                    command,
                    chat_id=chat_id,
                    board_path=self._board_path,
                    ledger_path=self._ledger_path,
                    runs_root=self._runs_root,
                )
            else:
                rendered = self._renderer(
                    command,
                    board_path=self._board_path,
                    ledger_path=self._ledger_path,
                    runs_root=self._runs_root,
                )
            for chunk in _message_chunks(rendered):
                self._api.send_message(chat_id=chat_id, text=chunk)
        self.publish_pending()
        return next_offset

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

    def run_forever(self, *, poll_timeout: int) -> None:
        """Long-poll until a transport failure or operator stop interrupts it."""
        offset: Optional[int] = None
        while True:
            offset = self.poll_once(offset=offset, timeout=poll_timeout)


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
    parser.add_argument("--once", action="store_true", help="Process one long-poll response, then exit")
    args = parser.parse_args(argv)

    try:
        settings = settings_from_environment(os.environ)
        transport = HttpsTelegramApi(settings.token)
        policy = telegram_control.load_policy(args.intake_policy) if args.intake_policy else None
        control = telegram_control.TelegramControl(policy)
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
        )
        if args.once:
            daemon.poll_once(offset=None, timeout=args.poll_timeout)
        else:
            daemon.run_forever(poll_timeout=args.poll_timeout)
    except (TelegramTransportError, ValueError):
        print("telegram daemon stopped: configuration or transport failure", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
