"""End-to-end proof of the reply-to-revise loop, through the real daemon.

Every layer runs for real - daemon, control surface, board, SQLite - with only
the HTTPS transport replaced. What this cannot prove is Telegram's own wire:
that a phone's reply actually arrives carrying `reply_to_message`, and that a
tapped button arrives as a `callback_query`. Those are Telegram's contract, and
only a real device can confirm them.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import board  # noqa: E402
import telegram_control  # noqa: E402
from interfaces import telegram_daemon as daemon  # noqa: E402
from support import BoardTestCase  # noqa: E402

CHAT = "42"


class ScriptedTransport:
    """A transport that hands over queued updates and records what was sent."""

    def __init__(self) -> None:
        self.queue: list[list[dict]] = []
        self.sent: list[tuple[str, str, dict | None]] = []
        self.edited: list[tuple[str, int, str]] = []
        self.documents: list[tuple[str, str, int]] = []
        self._next_message_id = 100

    def queue_updates(self, updates: list[dict]) -> None:
        self.queue.append(updates)

    def get_updates(self, *, offset, timeout):
        return self.queue.pop(0) if self.queue else []

    def send_message(self, *, chat_id, text, reply_markup=None):
        self._next_message_id += 1
        self.sent.append((chat_id, text, reply_markup))
        return self._next_message_id

    def edit_message(self, *, chat_id, message_id, text, reply_markup):
        self.edited.append((chat_id, message_id, text))

    def send_document(self, *, chat_id, filename, content, caption=""):
        self.documents.append((chat_id, filename, len(content)))
        self._next_message_id += 1
        return self._next_message_id

    # -- helpers for reading what the daemon produced -----------------------
    def last_markup(self):
        for _, _, markup in reversed(self.sent):
            if markup:
                return markup
        return None

    def texts(self):
        return [text for _, text, _ in self.sent]


class ReplyRevisionLoopTests(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.workspace = self.tmp / "ws"
        self.workspace.mkdir()
        (self.workspace / ".git").mkdir()
        (self.workspace / "celestial.html").write_text("<h1>space</h1>", encoding="utf-8")

        self.policy = telegram_control.IntakePolicy(
            workspaces={
                "sandbox": telegram_control.WorkspacePolicy(
                    "sandbox", self.workspace,
                    telegram_control.VerifyProfile("deliverable", "python verify.py", 120),
                ),
            },
            default_workspace="sandbox",
        )
        self.control = telegram_control.TelegramControl(
            self.policy, dashboard_url="http://100.64.0.1:8080",
        )
        self.transport = ScriptedTransport()
        self.daemon = daemon.TelegramDaemon(
            self.transport,
            daemon.TelegramSettings(token="t", authorized_chat_ids=frozenset({CHAT})),
            board_path=self.db_path,
            ledger_path=self.tmp / "ledger.jsonl",
            runs_root=self.tmp / "runs",
            handler=self.control.dispatch,
            callback_handler=self.control.dispatch_callback,
            completion_notifier=self.control.pending_completions,
            completion_recorder=self.control.record_completion,
            dashboard_url="http://100.64.0.1:8080",
            progress_opener=self.control.open_progress,
            progress_notifier=self.control.pending_progress,
            progress_starter=self.control.start_progress,
            progress_advancer=self.control.record_progress,
        )

    # -- scripted inbound shapes -------------------------------------------
    def message(self, text: str, *, update_id: int, reply_to: int | None = None) -> dict:
        message: dict = {
            "message_id": update_id + 500,
            "chat": {"id": int(CHAT)},
            "text": text,
        }
        if reply_to is not None:
            message["reply_to_message"] = {"message_id": reply_to}
        return {"update_id": update_id, "message": message}

    def tap(self, data: str, *, update_id: int) -> dict:
        return {
            "update_id": update_id,
            "callback_query": {
                "data": data,
                "from": {"id": int(CHAT)},
                "message": {"message_id": 900, "chat": {"id": int(CHAT)}},
            },
        }

    def finish(self, task_id: str) -> int:
        """Drive a task to a finished run the way the worker would."""
        self.assertIsNotNone(self.kb.claim_task(self.conn, task_id, claimer="fixture:1"))
        run_id = self.conn.execute(
            "SELECT MAX(id) AS id FROM task_runs WHERE task_id = ?", (task_id,)
        ).fetchone()["id"]
        self.conn.execute(
            "UPDATE task_runs SET status='done', outcome='completed', started_at=100, "
            "ended_at=200, summary='verify exit 0 in 1.0s', "
            "metadata='{\"verify_exit\": 0}' WHERE id = ?",
            (run_id,),
        )
        self.conn.execute("UPDATE tasks SET status='done' WHERE id = ?", (task_id,))
        self.conn.commit()
        return run_id

    def confirm_from_buttons(self, update_id: int) -> str:
        """Tap the Confirm button the daemon just sent, as a phone would."""
        markup = self.transport.last_markup()
        self.assertIsNotNone(markup, "staging did not offer any buttons")
        data = markup["inline_keyboard"][0][0]["callback_data"]
        self.assertTrue(data.startswith("confirm:"), data)
        self.transport.queue_updates([self.tap(data, update_id=update_id)])
        self.daemon.poll_once(offset=None, timeout=1)
        return data.split(":", 1)[1]

    # -- the loop ----------------------------------------------------------
    def test_a_tapped_reply_creates_a_follow_up_linked_to_its_original(self):
        # 1. A plain prompt is staged with buttons, and confirmed by tapping.
        self.transport.queue_updates([self.message("build a space page", update_id=1)])
        self.daemon.poll_once(offset=None, timeout=1)
        self.assertIn("workspace: sandbox", self.transport.texts()[0])
        self.confirm_from_buttons(2)

        original = self.conn.execute(
            "SELECT id FROM tasks ORDER BY created_at DESC LIMIT 1"
        ).fetchone()["id"]

        # 2. It runs and finishes, and the completion card is delivered.
        run_id = self.finish(original)
        self.transport.queue_updates([])
        self.daemon.poll_once(offset=None, timeout=1)
        card_message_id = self.conn.execute(
            "SELECT message_id FROM triai_completion_notifications WHERE task_id = ?",
            (original,),
        ).fetchone()["message_id"]

        # 3. The operator replies to that card with the change they want.
        self.transport.queue_updates([
            self.message("add an animated starfield", update_id=3, reply_to=card_message_id),
        ])
        self.daemon.poll_once(offset=None, timeout=1)
        staged = self.transport.texts()[-1]
        self.assertIn(f"Pending follow-up", staged)
        self.assertIn(original, staged)

        # 4. Tapping Confirm creates the follow-up, linked to the original.
        self.confirm_from_buttons(4)
        follow_up = self.conn.execute(
            "SELECT id FROM tasks WHERE id != ? ORDER BY created_at DESC LIMIT 1",
            (original,),
        ).fetchone()["id"]
        edges = self.conn.execute(
            "SELECT parent_id, child_id FROM task_links"
        ).fetchall()
        self.assertEqual(
            [(row["parent_id"], row["child_id"]) for row in edges],
            [(original, follow_up)],
        )
        # The revision inherits the workspace it is revising.
        self.assertEqual(
            self.conn.execute(
                "SELECT workspace_path FROM tasks WHERE id = ?", (follow_up,)
            ).fetchone()["workspace_path"],
            str(self.workspace),
        )

    def test_tapping_cancel_creates_nothing(self):
        self.transport.queue_updates([self.message("build a space page", update_id=1)])
        self.daemon.poll_once(offset=None, timeout=1)
        markup = self.transport.last_markup()
        cancel = markup["inline_keyboard"][0][1]["callback_data"]
        self.assertTrue(cancel.startswith("cancel:"))
        self.transport.queue_updates([self.tap(cancel, update_id=2)])
        self.daemon.poll_once(offset=None, timeout=1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    def test_a_bare_confirm_resolves_the_single_outstanding_request(self):
        self.transport.queue_updates([self.message("build a space page", update_id=1)])
        self.daemon.poll_once(offset=None, timeout=1)
        self.transport.queue_updates([self.message("/confirm", update_id=2)])
        self.daemon.poll_once(offset=None, timeout=1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)

    def test_a_reply_to_an_unknown_message_stages_an_unlinked_task(self):
        # A reply to something that is not a completion card must not guess at
        # a parent; it becomes an ordinary new task.
        self.transport.queue_updates([
            self.message("add a starfield", update_id=1, reply_to=9999),
        ])
        self.daemon.poll_once(offset=None, timeout=1)
        self.assertIn("Pending intake", self.transport.texts()[-1])
        self.confirm_from_buttons(2)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM task_links").fetchone()[0], 0,
        )

    def test_a_progress_card_is_opened_then_edited_in_place(self):
        self.transport.queue_updates([self.message("build a space page", update_id=1)])
        self.daemon.poll_once(offset=None, timeout=1)
        self.confirm_from_buttons(2)
        task_id = self.conn.execute(
            "SELECT id FROM tasks ORDER BY created_at DESC LIMIT 1"
        ).fetchone()["id"]

        # Claiming makes it running, so the next poll opens one card.
        self.assertIsNotNone(self.kb.claim_task(self.conn, task_id, claimer="fixture:1"))
        self.transport.queue_updates([])
        self.daemon.poll_once(offset=None, timeout=1)
        card = board.progress_card(self.conn, task_id=task_id, chat_id=CHAT)
        self.assertIsNotNone(card)
        opened_at = card["message_id"]

        # A poll with no phase change must not edit anything.
        before = len(self.transport.edited)
        self.transport.queue_updates([])
        self.daemon.poll_once(offset=None, timeout=1)
        self.assertEqual(len(self.transport.edited), before)

        # Finishing moves the phase, which edits that same message.
        self.conn.execute("UPDATE tasks SET status='done' WHERE id = ?", (task_id,))
        self.conn.commit()
        self.transport.queue_updates([])
        self.daemon.poll_once(offset=None, timeout=1)
        self.assertTrue(self.transport.edited)
        self.assertEqual(self.transport.edited[-1][1], opened_at)

    def test_a_produced_html_artifact_is_uploaded_with_the_card(self):
        self.transport.queue_updates([self.message("build a space page", update_id=1)])
        self.daemon.poll_once(offset=None, timeout=1)
        self.confirm_from_buttons(2)
        task_id = self.conn.execute(
            "SELECT id FROM tasks ORDER BY created_at DESC LIMIT 1"
        ).fetchone()["id"]
        run_id = self.finish(task_id)
        board.record_run_artifacts(
            self.conn, task_id=task_id, run_id=run_id,
            artifacts=[{"path": "celestial.html", "change": "??", "size_bytes": 14}],
        )
        self.transport.queue_updates([])
        self.daemon.poll_once(offset=None, timeout=1)
        self.assertEqual(
            [(name, size) for _, name, size in self.transport.documents],
            [("celestial.html", 14)],
        )


if __name__ == "__main__":
    unittest.main()
