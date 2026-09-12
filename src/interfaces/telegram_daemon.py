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
import time
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
    parser.add_argument("--once", action="store_true", help="Process one long-poll response, then exit")
    args = parser.parse_args(argv)

    try:
        settings = settings_from_sources(os.environ)
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
    except (TelegramTransportError, ValueError) as exc:
        print(startup_diagnostic(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
