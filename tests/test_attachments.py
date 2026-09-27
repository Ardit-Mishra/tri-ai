"""Files sent to the bot: parsed, named safely, and kept out of the workspace.

Until this existed, a message without a `text` field was dropped where it was
read - `if not isinstance(text, str): continue` - so a PDF or a logo produced
nothing at all, silently. Task `t_c9b08613` asked for a landing page "(I have
the logo with me)" and there was no path for that logo to reach the agent.

Two properties carry the weight here and both are adversarial: the filename
arrives from a remote sender and must never be used as a path, and an attachment
must never land in a workspace, because the worker refuses to claim a task whose
tree is dirty - intake would break execution.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import attachments  # noqa: E402


class NamesFromAnUntrustedSender(unittest.TestCase):
    def test_a_traversal_attempt_cannot_climb_out(self):
        for hostile in (
            r"..\..\..\Windows\System32\evil.dll",
            "../../../etc/passwd",
            "/etc/shadow",
            r"C:\Users\ardit\.ssh\id_ed25519",
        ):
            with self.subTest(hostile=hostile):
                safe = attachments.safe_name(hostile)
                self.assertNotIn("/", safe)
                self.assertNotIn("\\", safe)
                self.assertFalse(safe.startswith(".."))

    def test_the_extension_survives_because_it_is_useful(self):
        self.assertTrue(attachments.safe_name("Quarterly Report.PDF").endswith(".pdf"))
        self.assertTrue(attachments.safe_name("logo.svg").endswith(".svg"))

    def test_a_name_that_reduces_to_nothing_gets_a_fallback(self):
        for empty in ("", "...", "///", "\x00\x01"):
            with self.subTest(empty=empty):
                self.assertTrue(attachments.safe_name(empty, fallback="file"))

    def test_a_non_string_name_is_tolerated(self):
        # Telegram is a remote peer; the field may be absent or the wrong type.
        for junk in (None, 42, [], {}):
            with self.subTest(junk=junk):
                self.assertTrue(attachments.safe_name(junk, fallback="file"))

    def test_it_does_not_run_away_with_length(self):
        self.assertLessEqual(len(attachments.safe_name("a" * 500 + ".txt")), 70)

    def test_a_surviving_escape_is_refused_at_the_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                attachments.unique_path(Path(tmp), "../escaped.txt")

    def test_colliding_names_do_not_overwrite(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = attachments.unique_path(root, "logo.png")
            first.write_bytes(b"first")
            second = attachments.unique_path(root, "logo.png")
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), b"first")


class ReadingWhatAMessageCarries(unittest.TestCase):
    def test_a_plain_text_message_offers_nothing(self):
        self.assertEqual(attachments.from_message({"text": "hello"}), ())

    def test_a_document_is_found(self):
        found = attachments.from_message({
            "caption": "the spec",
            "document": {"file_id": "ABC123", "file_name": "spec.pdf",
                         "file_size": 4096, "mime_type": "application/pdf"},
        })
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].kind, "document")
        self.assertEqual(found[0].name, "spec.pdf")
        self.assertEqual(found[0].mime, "application/pdf")

    def test_a_photo_takes_the_largest_size(self):
        # Telegram sends thumbnails ascending; the smallest would be useless
        # as a logo.
        found = attachments.from_message({"photo": [
            {"file_id": "small", "width": 90, "height": 90, "file_size": 900},
            {"file_id": "big", "width": 1280, "height": 1280, "file_size": 90000},
        ]})
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].file_id, "big")

    def test_two_photos_do_not_collide_on_name(self):
        one = attachments.from_message({"photo": [{"file_id": "aaaaaaaa1", "width": 10, "height": 10}]})
        two = attachments.from_message({"photo": [{"file_id": "bbbbbbbb2", "width": 10, "height": 10}]})
        self.assertNotEqual(one[0].name, two[0].name)

    def test_voice_and_video_are_recognised(self):
        self.assertEqual(
            attachments.from_message({"voice": {"file_id": "v1"}})[0].kind, "voice")
        self.assertEqual(
            attachments.from_message({"video": {"file_id": "v2"}})[0].kind, "video")

    def test_malformed_blobs_are_ignored_not_raised(self):
        for junk in ({"document": "not a dict"}, {"photo": "nope"},
                     {"document": {"no_file_id": 1}}, {"photo": []}):
            with self.subTest(junk=junk):
                self.assertEqual(attachments.from_message(junk), ())

    def test_an_oversized_file_is_flagged_before_any_download(self):
        big = attachments.from_message({"document": {
            "file_id": "X", "file_name": "huge.zip",
            "file_size": attachments.MAX_ATTACHMENT_BYTES + 1,
        }})[0]
        self.assertTrue(big.too_large)


class AttachmentsStayOutOfWorkspaces(unittest.TestCase):
    """The property that keeps intake from breaking execution."""

    def test_the_root_is_under_the_runtime_directory(self):
        root = attachments.attachments_root(Path(r"C:\Users\someone\.tri-ai"))
        self.assertEqual(root.name, "attachments")
        self.assertIn(".tri-ai", str(root))

    def test_the_root_is_not_inside_a_workspace(self):
        # A file written into the workspace before the run would dirty the tree
        # and the worker's clean-tree precheck would skip the task - the intake
        # would break the execution it exists to feed.
        root = str(attachments.attachments_root(Path.home() / ".tri-ai")).lower()
        for workspace in ("tri-ai-sandbox", "projects", "genelens"):
            self.assertNotIn(workspace, root)


class TellingTheAgentWhatItWasGiven(unittest.TestCase):
    def stored(self, **kw):
        base = dict(path=Path(r"C:\Users\x\.tri-ai\attachments\1\claimed\logo.png"),
                    kind="photo", size=51200, original="logo.png")
        base.update(kw)
        return attachments.StoredAttachment(**base)

    def test_nothing_attached_adds_nothing(self):
        self.assertEqual(attachments.describe_for_prompt([]), "")

    def test_the_absolute_path_is_given(self):
        described = attachments.describe_for_prompt([self.stored()])
        self.assertIn(r"C:\Users\x\.tri-ai\attachments\1\claimed\logo.png", described)

    def test_it_says_to_copy_what_the_deliverable_needs(self):
        # Otherwise the agent reads the file, uses it, and the run records
        # nothing - the artifact only counts once it is in the workspace.
        described = attachments.describe_for_prompt([self.stored()])
        self.assertIn("copy it into the workspace", described)

    def test_the_count_is_stated(self):
        described = attachments.describe_for_prompt(
            [self.stored(), self.stored(original="spec.pdf", kind="document")])
        self.assertIn("2 files", described)


if __name__ == "__main__":
    unittest.main()
