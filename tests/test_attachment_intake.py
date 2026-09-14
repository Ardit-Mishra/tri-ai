"""A file sent to the bot reaches the agent instead of vanishing.

The daemon read `message["text"]` and skipped anything without it. A message
carrying a file puts its words in `caption`, so every PDF, photo and document
was dropped at that line - in silence, with no reply, no log and no record.
Task `t_c9b08613` asked for a landing page "(I have the logo with me)" and the
logo had no way in.

These drive the real `TelegramDaemon.poll_once` with a fake transport, so what
is asserted is the daemon's behaviour rather than a helper's.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "interfaces"))

import attachments  # noqa: E402
import telegram_daemon as daemon  # noqa: E402


class FakeTransport:
    """Records what was sent, and serves file bytes for a download."""

    def __init__(self, updates, *, files=None, fail_ids=()):
        self.updates = updates
        self.files = files or {}
        self.fail_ids = set(fail_ids)
        self.sent: list[tuple[str, str]] = []
        self.downloaded: list[str] = []

    def get_updates(self, *, offset, timeout):
        return self.updates

    def send_message(self, *, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text))
        return len(self.sent)

    def edit_message(self, *, chat_id, message_id, text, reply_markup):
        pass

    def download_file(self, file_id, destination, *, timeout=120, max_bytes=None):
        self.downloaded.append(file_id)
        if file_id in self.fail_ids:
            raise daemon.TelegramTransportError("download refused")
        body = self.files.get(file_id, b"x" * 64)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)
        return len(body)


class AttachmentIntake(unittest.TestCase):
    CHAT = "42"

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="triai-attach-"))
        self.runs = self.tmp / "runs"
        self.runs.mkdir(parents=True)
        self.seen: list[str] = []

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def settings(self):
        return daemon.TelegramSettings(
            token="t", authorized_chat_ids=frozenset({self.CHAT}),
        )

    def build(self, transport):
        return daemon.TelegramDaemon(
            transport, self.settings(),
            board_path=self.tmp / "board.db",
            ledger_path=self.tmp / "ledger.jsonl",
            runs_root=self.runs,
            renderer=lambda text, **kw: self.seen.append(text) or "ok",
        )

    def message(self, **fields):
        base = {"chat": {"id": int(self.CHAT)}}
        base.update(fields)
        return {"update_id": 1, "message": base}

    # -- the defect --------------------------------------------------------

    def test_a_document_with_a_caption_is_no_longer_dropped(self):
        transport = FakeTransport([self.message(
            caption="Build a landing page using this",
            document={"file_id": "F1", "file_name": "logo.png", "file_size": 64},
        )])
        self.build(transport).poll_once(offset=None, timeout=1)
        self.assertEqual(
            transport.downloaded, ["F1"],
            "the file was never fetched - the message was dropped as before",
        )
        self.assertTrue(self.seen, "the caption never reached the adapter")

    def test_the_prompt_names_the_file_on_disk(self):
        transport = FakeTransport([self.message(
            caption="Build a landing page using this logo",
            document={"file_id": "F1", "file_name": "logo.png", "file_size": 64},
        )])
        self.build(transport).poll_once(offset=None, timeout=1)
        prompt = self.seen[0]
        self.assertIn("Build a landing page using this logo", prompt)
        self.assertIn("logo.png", prompt)
        # An absolute path, because the agent's shell starts somewhere else.
        self.assertIn("attachments", prompt)

    def test_the_file_is_not_written_into_a_workspace(self):
        # The worker refuses to claim a task whose tree is dirty, so intake
        # that touched the workspace would break execution.
        transport = FakeTransport([self.message(
            caption="use this", document={"file_id": "F1", "file_name": "a.txt"},
        )])
        self.build(transport).poll_once(offset=None, timeout=1)
        landed = list(self.tmp.rglob("a.txt"))
        self.assertTrue(landed)
        for path in landed:
            self.assertIn("attachments", str(path))

    def test_a_file_with_a_caption_is_listed_exactly_once(self):
        """The prompt must not name a path that no longer exists.

        Files land in `pending/` and are moved to `claimed/` when a request
        takes them. An earlier version also kept the pre-move path, so a single
        logo arrived as "2 files" with one entry pointing into `pending/` at a
        file that had already been moved out of it. Task t_3bd071cc went to the
        agent that way.
        """
        transport = FakeTransport([self.message(
            caption="Build a landing page with this logo",
            document={"file_id": "F1", "file_name": "logo.png", "file_size": 64},
        )])
        self.build(transport).poll_once(offset=None, timeout=1)
        prompt = self.seen[0]
        self.assertIn("1 file", prompt)
        self.assertNotIn("2 files", prompt)
        self.assertEqual(
            prompt.count("logo.png"), 1,
            "the same file was listed more than once",
        )
        self.assertNotIn(
            "pending", prompt,
            "the prompt names a pending path the file has already left",
        )

    def test_every_path_in_the_prompt_exists_on_disk(self):
        # The strongest form of the above: whatever the agent is told to read
        # must actually be readable.
        transport = FakeTransport([self.message(
            caption="use these",
            document={"file_id": "F1", "file_name": "a.txt", "file_size": 10},
        )])
        self.build(transport).poll_once(offset=None, timeout=1)
        named = [
            line.strip().split("   (")[0].strip()
            for line in self.seen[0].splitlines()
            if "attachments" in line and line.strip().startswith(str(self.tmp)[:3])
        ]
        self.assertTrue(named, "no absolute path was given to the agent")
        for path in named:
            self.assertTrue(Path(path).is_file(), f"prompt names a missing file: {path}")


    # -- files with no words -----------------------------------------------

    def test_a_file_with_no_caption_is_held_and_acknowledged(self):
        transport = FakeTransport([self.message(
            document={"file_id": "F1", "file_name": "logo.png", "file_size": 64},
        )])
        self.build(transport).poll_once(offset=None, timeout=1)
        self.assertFalse(self.seen, "a bare file should not become a task")
        self.assertTrue(transport.sent, "the operator was told nothing")
        reply = transport.sent[0][1]
        self.assertIn("logo.png", reply)
        self.assertIn("Held", reply)

    def test_a_held_file_attaches_to_the_next_request(self):
        held = FakeTransport([self.message(
            document={"file_id": "F1", "file_name": "logo.png"})])
        worker = self.build(held)
        worker.poll_once(offset=None, timeout=1)

        later = FakeTransport([self.message(text="Build me a landing page")])
        self.build(later).poll_once(offset=None, timeout=1)
        self.assertTrue(self.seen)
        self.assertIn("logo.png", self.seen[-1])

    def test_a_held_file_attaches_once_only(self):
        held = FakeTransport([self.message(document={"file_id": "F1", "file_name": "logo.png"})])
        self.build(held).poll_once(offset=None, timeout=1)

        self.build(FakeTransport([self.message(text="first request")])).poll_once(offset=None, timeout=1)
        self.build(FakeTransport([self.message(text="second request")])).poll_once(offset=None, timeout=1)
        self.assertIn("logo.png", self.seen[-2])
        self.assertNotIn(
            "logo.png", self.seen[-1],
            "the file re-attached to a later, unrelated request",
        )

    # -- failures are spoken, not swallowed --------------------------------

    def test_a_failed_download_is_reported(self):
        transport = FakeTransport(
            [self.message(caption="use it",
                          document={"file_id": "BAD", "file_name": "x.pdf"})],
            fail_ids={"BAD"},
        )
        self.build(transport).poll_once(offset=None, timeout=1)
        self.assertTrue(
            any("x.pdf" in text for _, text in transport.sent),
            "a file that failed to save was never mentioned to the operator",
        )

    def test_an_oversized_file_is_refused_without_downloading(self):
        transport = FakeTransport([self.message(
            caption="use it",
            document={"file_id": "HUGE", "file_name": "big.zip",
                      "file_size": attachments.MAX_ATTACHMENT_BYTES + 1},
        )])
        self.build(transport).poll_once(offset=None, timeout=1)
        self.assertEqual(
            transport.downloaded, [],
            "an oversized file was fetched anyway",
        )
        self.assertTrue(any("big.zip" in t for _, t in transport.sent))

    # -- nothing else changed ----------------------------------------------

    def test_a_plain_text_command_is_unaffected(self):
        transport = FakeTransport([self.message(text="/status")])
        self.build(transport).poll_once(offset=None, timeout=1)
        self.assertEqual(self.seen, ["/status"])
        self.assertEqual(transport.downloaded, [])

    def test_a_slash_command_does_not_get_attachment_text_appended(self):
        held = FakeTransport([self.message(document={"file_id": "F1", "file_name": "n.txt"})])
        self.build(held).poll_once(offset=None, timeout=1)
        later = FakeTransport([self.message(text="/status")])
        self.build(later).poll_once(offset=None, timeout=1)
        self.assertEqual(
            self.seen[-1], "/status",
            "a board query is not a task and has nothing to attach files to",
        )

    def test_an_unauthorized_chat_cannot_store_anything(self):
        transport = FakeTransport([{"update_id": 1, "message": {
            "chat": {"id": 999}, "caption": "hi",
            "document": {"file_id": "F1", "file_name": "evil.exe"},
        }}])
        self.build(transport).poll_once(offset=None, timeout=1)
        self.assertEqual(transport.downloaded, [])
        self.assertEqual(list(self.tmp.rglob("evil.exe")), [])


if __name__ == "__main__":
    unittest.main()
