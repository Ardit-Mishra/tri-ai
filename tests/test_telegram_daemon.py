"""Proofs for the external, read-only Telegram long-poll transport."""

from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from interfaces import telegram_daemon as daemon  # noqa: E402


class FakeTransport:
    def __init__(self, updates: list[dict[str, object]]) -> None:
        self.updates = updates
        self.get_calls: list[tuple[int | None, int]] = []
        self.sent: list[tuple[str, str]] = []

    def get_updates(self, *, offset: int | None, timeout: int) -> list[dict[str, object]]:
        self.get_calls.append((offset, timeout))
        return self.updates

    def send_message(self, *, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


class FakeResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class TelegramDaemonTests(unittest.TestCase):
    def settings(self) -> daemon.TelegramSettings:
        return daemon.TelegramSettings(token="test-token", authorized_chat_ids=frozenset({"42"}))

    def test_missing_token_or_authorized_chat_configuration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, daemon.TOKEN_ENV):
            daemon.settings_from_environment({daemon.AUTHORIZED_CHAT_ID_ENV: "42"})
        with self.assertRaisesRegex(ValueError, daemon.AUTHORIZED_CHAT_ID_ENV):
            daemon.settings_from_environment({daemon.TOKEN_ENV: "test-token"})

    def test_authorized_update_reaches_the_read_surface_and_returns_its_response(self):
        transport = FakeTransport([{"update_id": 10, "message": {"chat": {"id": 42}, "text": "/status"}}])
        calls: list[str] = []
        worker = daemon.TelegramDaemon(
            transport, self.settings(), board_path="board.db", ledger_path="ledger.jsonl", runs_root="runs",
            renderer=lambda text, **kwargs: calls.append(text) or "current board",
        )
        self.assertEqual(worker.poll_once(offset=None, timeout=30), 11)
        self.assertEqual(calls, ["/status"])
        self.assertEqual(transport.sent, [("42", "current board")])

    def test_unauthorized_update_is_dropped_before_the_adapter_is_called(self):
        transport = FakeTransport([{"update_id": 10, "message": {"chat": {"id": 99}, "text": "/status"}}])
        worker = daemon.TelegramDaemon(
            transport, self.settings(), board_path="board.db", ledger_path="ledger.jsonl", runs_root="runs",
            renderer=lambda *args, **kwargs: self.fail("unauthorized update reached adapter"),
        )
        self.assertEqual(worker.poll_once(offset=None, timeout=30), 11)
        self.assertEqual(transport.sent, [])

    def test_command_addressed_to_a_bot_is_normalized_before_dispatch(self):
        transport = FakeTransport([{"update_id": 4, "message": {"chat": {"id": 42}, "text": "/task@tri_ai_bot abc"}}])
        calls: list[str] = []
        worker = daemon.TelegramDaemon(
            transport, self.settings(), board_path="board.db", ledger_path="ledger.jsonl", runs_root="runs",
            renderer=lambda text, **kwargs: calls.append(text) or "detail",
        )
        worker.poll_once(offset=None, timeout=30)
        self.assertEqual(calls, ["/task abc"])

    def test_full_log_response_is_chunked_without_truncation(self):
        transport = FakeTransport([{"update_id": 1, "message": {"chat": {"id": 42}, "text": "/logs task"}}])
        response = "x" * (daemon.MAX_MESSAGE_CHARS + 13)
        worker = daemon.TelegramDaemon(
            transport, self.settings(), board_path="board.db", ledger_path="ledger.jsonl", runs_root="runs",
            renderer=lambda *args, **kwargs: response,
        )
        worker.poll_once(offset=None, timeout=30)
        self.assertEqual("".join(text for _, text in transport.sent), response)
        self.assertTrue(all(len(text) <= daemon.MAX_MESSAGE_CHARS for _, text in transport.sent))

    def test_https_client_uses_post_json_and_rejects_a_bot_api_failure(self):
        requests = []

        def opener(req, *, timeout):
            requests.append((req, timeout))
            return FakeResponse({"ok": True, "result": []})

        api = daemon.HttpsTelegramApi("test-token", opener=opener)
        self.assertEqual(api.get_updates(offset=7, timeout=30), [])
        req, timeout = requests[0]
        self.assertTrue(req.full_url.startswith("https://api.telegram.org/bot"))
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data.decode("utf-8"))["offset"], 7)
        self.assertEqual(timeout, 40)

        rejected = daemon.HttpsTelegramApi("test-token", opener=lambda *args, **kwargs: FakeResponse({"ok": False}))
        with self.assertRaises(daemon.TelegramTransportError):
            rejected.send_message(chat_id="42", text="status")


class TransportBoundaryIsNarrow(unittest.TestCase):
    source = Path(__file__).resolve().parents[1] / "src" / "interfaces" / "telegram_daemon.py"

    def test_daemon_has_no_board_mutator_or_process_spawn(self):
        tree = ast.parse(self.source.read_text(encoding="utf-8"))
        forbidden_imports = {"board", "ledger", "subprocess"}
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend(alias.name for alias in node.names if alias.name.split(".")[0] in forbidden_imports)
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in forbidden_imports:
                    found.append(node.module)
        self.assertEqual(found, [])

    def test_daemon_handler_seam_can_reach_control_without_direct_board_access(self):
        source = self.source.read_text(encoding="utf-8")
        self.assertIn("handler=", source)
        self.assertNotIn("import board", source)


if __name__ == "__main__":
    unittest.main()
