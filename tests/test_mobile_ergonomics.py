"""Proofs for tap-to-confirm intake, the in-place progress card, and file delivery.

The friction being removed is real: confirming a task meant copying a hex id on a
phone keyboard, and knowing whether anything was happening meant typing /status
until it changed.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import board  # noqa: E402
import completion_report  # noqa: E402
import progress_card  # noqa: E402
import telegram_control  # noqa: E402
from support import BoardTestCase  # noqa: E402


class KeyboardShapeTests(unittest.TestCase):
    def test_confirm_keyboard_carries_both_decisions_for_one_request(self):
        keys = telegram_control.confirm_keyboard("p_abc")["inline_keyboard"][0]
        self.assertEqual(
            [key["callback_data"] for key in keys], ["confirm:p_abc", "cancel:p_abc"],
        )

    def test_workspace_keyboard_offers_every_configured_alias(self):
        keys = telegram_control.workspace_keyboard(["sandbox", "genclarus", "tri-ai"])
        flat = [key["callback_data"] for row in keys["inline_keyboard"] for key in row]
        self.assertEqual(flat, ["ws:sandbox", "ws:genclarus", "ws:tri-ai"])

    def test_completion_keyboard_offers_revision_and_the_verify_log(self):
        keys = telegram_control.completion_keyboard("t_1")["inline_keyboard"][0]
        self.assertEqual(
            [key["callback_data"] for key in keys], ["revise:t_1", "log:t_1"],
        )

    def test_a_staged_reply_is_still_its_own_text(self):
        # Every existing caller treats a reply as a string; buttons are additive.
        reply = telegram_control.StagedReply("hello", {"inline_keyboard": []})
        self.assertIsInstance(reply, str)
        self.assertEqual(reply.split(), ["hello"])
        self.assertEqual(reply.reply_markup, {"inline_keyboard": []})


class PendingActionTests(BoardTestCase):
    def stage(self, prompt="build a page", action_id="p_a", ttl=300):
        board.create_pending_action(
            self.conn, action_id=action_id, chat_id="chat-1", action="run",
            payload={
                "parent_task_id": None, "title": "Telegram: sandbox", "prompt": prompt,
                "workspace": str(self.tmp), "verify_command": "true", "verify_timeout": 30,
            },
            expires_at=int(time.time()) + ttl,
        )
        return action_id

    def test_outstanding_requests_are_listed_for_their_own_chat(self):
        first = self.stage("one", action_id="p_a")
        second = self.stage("two longer prompt", action_id="p_b")
        outstanding = board.pending_actions_for_chat(self.conn, "chat-1")
        self.assertEqual({item["id"] for item in outstanding}, {first, second})
        self.assertEqual(board.pending_actions_for_chat(self.conn, "chat-2"), ())

    def test_an_expired_request_is_not_offered_for_confirmation(self):
        # The board refuses to create one already expired, so age it instead:
        # a request that was valid when staged must stop being offered later.
        self.stage("stale", action_id="p_stale", ttl=60)
        self.assertEqual(len(board.pending_actions_for_chat(self.conn, "chat-1")), 1)
        later = int(time.time()) + 120
        self.assertEqual(board.pending_actions_for_chat(self.conn, "chat-1", now=later), ())

    def test_cancelling_withdraws_the_request_and_creates_nothing(self):
        action_id = self.stage()
        result = board.cancel_pending_action(self.conn, action_id=action_id, chat_id="chat-1")
        self.assertTrue(result.changed)
        self.assertEqual(board.pending_actions_for_chat(self.conn, "chat-1"), ())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    def test_another_chat_cannot_cancel_a_request_it_did_not_raise(self):
        action_id = self.stage()
        result = board.cancel_pending_action(self.conn, action_id=action_id, chat_id="chat-2")
        self.assertFalse(result.changed)
        self.assertEqual(len(board.pending_actions_for_chat(self.conn, "chat-1")), 1)

    def test_a_cancelled_request_can_no_longer_be_confirmed(self):
        action_id = self.stage()
        board.cancel_pending_action(self.conn, action_id=action_id, chat_id="chat-1")
        result = board.confirm_pending_action(self.conn, action_id=action_id, chat_id="chat-1")
        self.assertFalse(result.changed)


class ProgressCardTests(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task = board.create_task(
            self.conn, title="Telegram: sandbox", prompt="build a space page",
            repo=self.tmp, verify_command="true", verify_timeout=30,
        )

    def row(self, **over):
        base = {
            "task_id": self.task, "task_status": "running", "run_status": "running",
            "body": "build a space page", "title": "Telegram: sandbox",
            "workspace_path": str(self.tmp), "started_at": 1000,
        }
        base.update(over)
        return base

    def test_phase_is_read_from_state_never_guessed(self):
        self.assertEqual(progress_card.phase_for(self.row()), "agent_active")
        self.assertEqual(
            progress_card.phase_for(self.row(run_status=None)), "claimed",
        )
        self.assertEqual(
            progress_card.phase_for(self.row(task_status="done")), "done",
        )

    def test_a_card_names_the_prompt_the_workspace_and_the_elapsed_time(self):
        card = progress_card.render(self.row(), phase="agent_active", now=1252)
        self.assertIn("build a space page", card.text)
        self.assertIn("Building", card.text)
        self.assertIn("running 4m 12s", card.text)
        self.assertFalse(card.closed)

    def test_a_terminal_phase_closes_the_card(self):
        card = progress_card.render(
            self.row(task_status="done"), phase="done", now=1252,
        )
        self.assertTrue(card.closed)
        self.assertIn("took 4m 12s", card.text)

    def test_one_card_per_task_per_chat(self):
        self.assertTrue(board.record_progress_card(
            self.conn, task_id=self.task, chat_id="chat-1", message_id=10, phase="claimed",
        ))
        self.assertFalse(board.record_progress_card(
            self.conn, task_id=self.task, chat_id="chat-1", message_id=11, phase="claimed",
        ))
        stored = board.progress_card(self.conn, task_id=self.task, chat_id="chat-1")
        self.assertEqual(stored["message_id"], 10)

    def test_an_unchanged_phase_costs_no_edit(self):
        board.record_progress_card(
            self.conn, task_id=self.task, chat_id="chat-1", message_id=10, phase="claimed",
        )
        self.assertFalse(board.advance_progress_card(
            self.conn, task_id=self.task, chat_id="chat-1", phase="claimed",
        ))
        self.assertTrue(board.advance_progress_card(
            self.conn, task_id=self.task, chat_id="chat-1", phase="agent_active",
        ))

    def test_a_closed_card_stops_advancing(self):
        board.record_progress_card(
            self.conn, task_id=self.task, chat_id="chat-1", message_id=10, phase="claimed",
        )
        board.advance_progress_card(
            self.conn, task_id=self.task, chat_id="chat-1", phase="done", closed=True,
        )
        self.assertFalse(board.advance_progress_card(
            self.conn, task_id=self.task, chat_id="chat-1", phase="failed",
        ))
        self.assertEqual(board.open_progress_cards(self.conn, "chat-1"), ())


class DocumentDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def test_small_text_artifacts_are_selected_for_upload(self):
        picked = completion_report.deliverable_documents(
            [{"path": "celestial.html", "size_bytes": 27408}], str(self.root),
        )
        self.assertEqual([item["path"] for item in picked], ["celestial.html"])
        self.assertTrue(Path(picked[0]["absolute"]).is_relative_to(self.root))

    def test_binary_and_oversize_artifacts_stay_links(self):
        picked = completion_report.deliverable_documents(
            [
                {"path": "preview.png", "size_bytes": 524493},
                {"path": "huge.html", "size_bytes": completion_report.DOCUMENT_MAX_BYTES + 1},
                {"path": "empty.html", "size_bytes": 0},
            ],
            str(self.root),
        )
        self.assertEqual(picked, ())

    def test_an_artifact_escaping_its_workspace_is_never_uploaded(self):
        picked = completion_report.deliverable_documents(
            [{"path": "../../secrets.txt", "size_bytes": 12}], str(self.root),
        )
        self.assertEqual(picked, ())

    def test_a_completion_card_carries_its_deliverable_documents(self):
        card = completion_report.render({
            "task_id": "t_1", "run_id": 1, "title": "t", "body": "build a page",
            "outcome": "completed", "summary": "verify exit 0", "error": None,
            "started_at": 1, "ended_at": 2, "metadata": "{}",
            "workspace_path": str(self.root),
            "artifacts": ({"path": "README.md", "size_bytes": 400},),
        })
        self.assertEqual([item["path"] for item in card.documents], ["README.md"])


if __name__ == "__main__":
    unittest.main()


class IntakePreflightTests(unittest.TestCase):
    """A prompt naming a file absent from its target is flagged before it runs."""

    def setUp(self) -> None:
        import tempfile, shutil
        self.root = Path(tempfile.mkdtemp(prefix="triai-preflight-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "celestial.html").write_text("<h1>hi</h1>", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "board.py").write_text("x = 1", encoding="utf-8")

    def test_a_prompt_naming_no_file_never_warns(self):
        import intake_preflight
        result = intake_preflight.check("build me a landing page about space", self.root)
        self.assertEqual(result.named, ())
        self.assertFalse(result.warns)

    def test_a_named_file_that_is_present_does_not_warn(self):
        import intake_preflight
        result = intake_preflight.check("In celestial.html add a starfield", self.root)
        self.assertEqual(result.present, ("celestial.html",))
        self.assertFalse(result.warns)

    def test_the_mis_aimed_case_is_flagged(self):
        # This is the real failure: an edit aimed at a workspace that does not
        # hold the file, which silently became "write a new one from scratch".
        import intake_preflight
        result = intake_preflight.check("In nebula.html add motion", self.root)
        self.assertEqual(result.missing, ("nebula.html",))
        self.assertTrue(result.warns)
        line = intake_preflight.warning_line(result, "sandbox")
        self.assertIn("nebula.html", line)
        self.assertIn("sandbox", line)

    def test_a_nested_path_resolves_against_the_workspace(self):
        import intake_preflight
        self.assertFalse(intake_preflight.check("edit src/board.py", self.root).warns)

    def test_a_mix_of_present_and_new_files_is_ordinary_work(self):
        # Editing one file while creating another is normal; only a prompt
        # where nothing it referred to exists is worth interrupting for.
        import intake_preflight
        result = intake_preflight.check(
            "edit celestial.html and create nebula.html", self.root,
        )
        self.assertEqual(result.present, ("celestial.html",))
        self.assertEqual(result.missing, ("nebula.html",))
        self.assertFalse(result.warns)
        self.assertIsNone(intake_preflight.warning_line(result, "sandbox"))

    def test_prose_abbreviations_are_not_mistaken_for_files(self):
        import intake_preflight
        self.assertEqual(
            intake_preflight.check("make it faster, e.g. by caching", self.root).named, (),
        )

    def test_an_unreadable_workspace_reports_names_without_claiming_absence(self):
        import intake_preflight
        result = intake_preflight.check("In celestial.html add motion", self.root / "gone")
        self.assertEqual(result.named, ("celestial.html",))
        self.assertEqual(result.present, ())
        self.assertEqual(result.missing, ())
        self.assertFalse(result.warns)


class DeliveryGuaranteeTests(BoardTestCase):
    """The delivery guarantee is at-least-once, and the code says so.

    Found in review: the docstrings claimed exactly-once while the caller sends
    first and records second. These tests pin the guarantee that actually holds,
    including the window that duplicates, so the claim cannot drift back.
    """

    def setUp(self) -> None:
        super().setUp()
        self.task = board.create_task(
            self.conn, title="Telegram: sandbox", prompt="build a page",
            repo=self.tmp, verify_command="true", verify_timeout=30,
        )
        self.assertIsNotNone(self.kb.claim_task(self.conn, self.task, claimer="fixture:1"))
        self.run_id = self.conn.execute(
            "SELECT MAX(id) AS id FROM task_runs WHERE task_id = ?", (self.task,)
        ).fetchone()["id"]
        self.conn.execute(
            "UPDATE task_runs SET status='done', outcome='completed', started_at=1, "
            "ended_at=2 WHERE id = ?", (self.run_id,),
        )
        self.conn.commit()

    def test_a_crash_between_send_and_record_re_sends_rather_than_loses(self):
        # The daemon sends, then records. Simulating a crash in between - by
        # never recording - must leave the completion pending, because losing a
        # finished task is the worse failure of the two.
        self.assertEqual(
            [row["task_id"] for row in board.pending_completions_for_chat(self.conn, "chat-1")],
            [self.task],
        )
        # ... crash here, no record written ...
        self.assertEqual(
            [row["task_id"] for row in board.pending_completions_for_chat(self.conn, "chat-1")],
            [self.task],
            "a completion lost to a crash must still be pending",
        )
        board.record_completion_notification(
            self.conn, task_id=self.task, run_id=self.run_id,
            chat_id="chat-1", message_id=7,
        )
        self.assertEqual(board.pending_completions_for_chat(self.conn, "chat-1"), ())

    def test_the_docstrings_state_the_guarantee_that_actually_holds(self):
        # The claim and the behaviour drifted apart once - the docs said
        # exactly-once while the caller sent first and recorded second. Assert
        # the guarantee is named, and that any mention of exactly-once is a
        # denial rather than a claim.
        import inspect
        import interfaces.telegram_daemon as daemon_module
        for doc in (
            board.pending_completions_for_chat.__doc__,
            inspect.getdoc(daemon_module.TelegramDaemon.publish_completions),
            inspect.getdoc(daemon_module.TelegramDaemon.publish_progress),
        ):
            self.assertIsNotNone(doc)
            lowered = " ".join(doc.lower().split())
            self.assertIn("at-least-once", lowered)
            for index in range(len(lowered)):
                index = lowered.find("exactly-once", index)
                if index == -1:
                    break
                self.assertIn(
                    "not exactly-once", lowered[max(0, index - 4):index + 12],
                    f"exactly-once is claimed, not denied, in: {doc[:80]!r}",
                )


class CallbackChatBindingTests(BoardTestCase):
    """A button on a completion card only acts for the chat that received it."""

    def setUp(self) -> None:
        super().setUp()
        self.task = board.create_task(
            self.conn, title="Telegram: sandbox", prompt="build a page",
            repo=self.tmp, verify_command="true", verify_timeout=30,
        )
        board.record_completion_notification(
            self.conn, task_id=self.task, run_id=1, chat_id="chat-1", message_id=11,
        )

    def test_the_notified_chat_is_recognised(self):
        self.assertTrue(
            board.chat_was_notified(self.conn, chat_id="chat-1", task_id=self.task)
        )

    def test_a_chat_that_was_never_told_is_not(self):
        self.assertFalse(
            board.chat_was_notified(self.conn, chat_id="chat-2", task_id=self.task)
        )
        self.assertFalse(
            board.chat_was_notified(self.conn, chat_id="chat-1", task_id="t_unknown")
        )

    def test_log_and_revise_refuse_a_task_this_chat_never_received(self):
        control = telegram_control.TelegramControl()
        for data in (f"log:{self.task}", f"revise:{self.task}"):
            response = control.dispatch_callback(
                data, chat_id="chat-2", board_path=self.db_path,
                ledger_path=self.tmp / "ledger.jsonl", runs_root=self.tmp / "runs",
            )
            self.assertIn("not delivered to this chat", response.text)
