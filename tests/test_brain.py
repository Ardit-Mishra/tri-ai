"""Contracts for Tri-AI's local, provenance-preserving Brain kernel."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memory import brain  # noqa: E402


class BrainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="triai-brain-"))
        self.conn = brain.connect(self.root / "brain.db")
        self.addCleanup(self.conn.close)

    def test_capture_is_deduplicated_but_every_inbox_receipt_is_retained(self):
        first = brain.capture(
            self.conn,
            "GenClarus keeps the gene summary pinned above variant results.",
            source="telegram:42",
            project="genclarus",
        )
        second = brain.capture(
            self.conn,
            "GenClarus keeps the gene summary pinned above variant results.",
            source="telegram:42",
            project="genclarus",
        )

        self.assertEqual(first.item_id, second.item_id)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM brain_items").fetchone()[0], 1
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM brain_inbox").fetchone()[0], 2
        )

    def test_search_returns_provenance_and_marks_unreviewed_memory(self):
        saved = brain.capture(
            self.conn,
            "Peptide-MHC production uses the Vercel release preview until DNS cutover.",
            source="telegram:42",
            project="peptide-mhc",
        )

        hits = brain.search(self.conn, "Vercel DNS", limit=5)

        self.assertEqual([hit.item_id for hit in hits], [saved.item_id])
        self.assertEqual(hits[0].trust, "unreviewed")
        self.assertEqual(hits[0].source, "telegram:42")
        self.assertEqual(hits[0].citation, f"brain:{saved.item_id}")

    def test_context_pack_is_bounded_and_keeps_trust_and_citations_visible(self):
        for index in range(4):
            brain.capture(
                self.conn,
                f"Tri-AI agent routing evidence item {index} with verifier provenance.",
                source=f"ledger:line-{index + 1}",
                project="tri-ai",
            )

        pack = brain.context_pack(self.conn, "agent routing", max_characters=240)

        self.assertLessEqual(len(pack.text), 240)
        self.assertTrue(pack.item_ids)
        self.assertTrue(pack.truncated)
        self.assertIn("UNREVIEWED", pack.text)
        self.assertIn("brain:", pack.text)

    def test_graph_edges_require_existing_items_and_retain_evidence(self):
        source = brain.capture(
            self.conn, "Telegram is the primary mobile command path.", source="operator"
        )
        target = brain.capture(
            self.conn, "The dashboard is the visual control surface.", source="operator"
        )

        edge = brain.link(
            self.conn,
            source.item_id,
            target.item_id,
            relation="controls",
            evidence_item_id=source.item_id,
        )

        self.assertEqual(edge.relation, "controls")
        self.assertEqual(brain.neighbors(self.conn, source.item_id)[0], edge)
        with self.assertRaisesRegex(ValueError, "unknown brain item"):
            brain.link(
                self.conn,
                "missing",
                target.item_id,
                relation="controls",
                evidence_item_id=source.item_id,
            )

    def test_brain_has_no_board_or_process_execution_capability(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "memory" / "brain.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("import board", "subprocess", "os.system", "Popen("):
            self.assertNotIn(forbidden, source)

    def test_obvious_credentials_are_rejected_before_persistence(self):
        with self.assertRaisesRegex(ValueError, "credential"):
            brain.capture(
                self.conn,
                "OPENAI_API_KEY=sk-example-secret-that-must-not-enter-memory",
                source="telegram:42",
            )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM brain_inbox").fetchone()[0], 0
        )

    def test_read_only_connection_cannot_mutate_the_brain(self):
        brain.capture(self.conn, "A durable fact", source="operator")
        self.conn.close()
        readonly = brain.connect_read_only(self.root / "brain.db")
        self.addCleanup(readonly.close)

        self.assertEqual(brain.stats(readonly).item_count, 1)
        with self.assertRaises(Exception):
            readonly.execute("DELETE FROM brain_items")


if __name__ == "__main__":
    unittest.main()
