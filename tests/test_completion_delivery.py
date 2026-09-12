"""Proofs for produced-artifact capture, completion delivery, and artifact serving.

Together these close the gap where a task finished, wrote a real file, and told
nobody. Each layer is proved on evidence the board actually holds.
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path
from urllib import error, request

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import board  # noqa: E402
import completion_report  # noqa: E402
from dashboard import jarvis_terminal as terminal  # noqa: E402
from dashboard import jarvis_web as web  # noqa: E402
from support import BoardTestCase  # noqa: E402


class ArtifactRecordTests(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task = board.create_task(
            self.conn, title="Telegram: tri-ai", prompt="build a diwali page",
            repo=self.tmp, verify_command="true", verify_timeout=30,
        )

    def test_artifacts_round_trip_with_change_and_size(self):
        written = board.record_run_artifacts(
            self.conn, task_id=self.task, run_id=1,
            artifacts=[
                {"path": "diwali.html", "change": "??", "size_bytes": 11770},
                {"path": "notes/plan.md", "change": "M", "size_bytes": None},
            ],
        )
        self.assertEqual(written, 2)
        rows = board.run_artifacts(self.conn, task_id=self.task, run_id=1)
        self.assertEqual([r["path"] for r in rows], ["diwali.html", "notes/plan.md"])
        self.assertEqual(rows[0]["change"], "??")
        self.assertEqual(rows[0]["size_bytes"], 11770)
        self.assertIsNone(rows[1]["size_bytes"])

    def test_recording_is_idempotent_for_the_same_path(self):
        for _ in range(2):
            board.record_run_artifacts(
                self.conn, task_id=self.task, run_id=1,
                artifacts=[{"path": "diwali.html", "change": "??", "size_bytes": 10}],
            )
        self.assertEqual(len(board.run_artifacts(self.conn, task_id=self.task, run_id=1)), 1)

    def test_malformed_artifacts_are_refused_before_any_write(self):
        for bad in ([{"path": "", "change": "??"}], [{"path": "x", "change": ""}],
                    [{"path": "x", "change": "??", "size_bytes": "big"}]):
            with self.assertRaises(ValueError):
                board.record_run_artifacts(
                    self.conn, task_id=self.task, run_id=1, artifacts=bad,
                )
        self.assertEqual(board.run_artifacts(self.conn, task_id=self.task), ())


class CompletionNotificationTests(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task = board.create_task(
            self.conn, title="Telegram: tri-ai", prompt="build a diwali page",
            repo=self.tmp, verify_command="true", verify_timeout=30,
        )
        claimed = self.kb.claim_task(self.conn, self.task, claimer="fixture:1")
        self.assertIsNotNone(claimed)
        self.run_id = self.conn.execute(
            "SELECT MAX(id) AS id FROM task_runs WHERE task_id = ?", (self.task,)
        ).fetchone()["id"]
        self.conn.execute(
            "UPDATE task_runs SET status='done', outcome='completed', ended_at=200, "
            "started_at=100, summary='verify exit 0 in 1.0s', "
            "metadata='{\"verify_exit\": 0, \"model\": \"auto/best-free\"}' WHERE id = ?",
            (self.run_id,),
        )
        self.conn.commit()

    def test_a_finished_run_is_pending_until_it_is_delivered(self):
        pending = board.pending_completions_for_chat(self.conn, "chat-1")
        self.assertEqual([row["task_id"] for row in pending], [self.task])

        self.assertTrue(board.record_completion_notification(
            self.conn, task_id=self.task, run_id=self.run_id,
            chat_id="chat-1", message_id=77,
        ))
        self.assertEqual(board.pending_completions_for_chat(self.conn, "chat-1"), ())

    def test_delivery_is_per_chat_and_never_repeats(self):
        board.record_completion_notification(
            self.conn, task_id=self.task, run_id=self.run_id,
            chat_id="chat-1", message_id=77,
        )
        # A second chat has still not been told.
        self.assertEqual(len(board.pending_completions_for_chat(self.conn, "chat-2")), 1)
        # Re-recording the same delivery is a no-op, not a duplicate send.
        self.assertFalse(board.record_completion_notification(
            self.conn, task_id=self.task, run_id=self.run_id,
            chat_id="chat-1", message_id=78,
        ))

    def test_pending_rows_carry_the_artifacts_the_run_produced(self):
        board.record_run_artifacts(
            self.conn, task_id=self.task, run_id=self.run_id,
            artifacts=[{"path": "diwali.html", "change": "??", "size_bytes": 11770}],
        )
        row = board.pending_completions_for_chat(self.conn, "chat-1")[0]
        self.assertEqual([a["path"] for a in row["artifacts"]], ["diwali.html"])

    def test_an_unfinished_run_is_never_announced(self):
        self.conn.execute(
            "UPDATE task_runs SET status='running', ended_at=NULL WHERE id = ?", (self.run_id,),
        )
        self.conn.commit()
        self.assertEqual(board.pending_completions_for_chat(self.conn, "chat-1"), ())


class CompletionCardTests(unittest.TestCase):
    def row(self, **overrides):
        base = {
            "task_id": "t_63cfab7a", "run_id": 6, "title": "Telegram: tri-ai",
            "body": "build a html page saying hello everyone in celebration of diwali",
            "outcome": "completed", "summary": "verify exit 0 in 178.79s",
            "error": None, "started_at": 100, "ended_at": 279,
            "metadata": json.dumps({"verify_exit": 0, "model": "auto/best-free", "provider": "custom"}),
            "artifacts": ({"path": "diwali.html", "change": "??", "size_bytes": 11770},),
        }
        base.update(overrides)
        return base

    def test_card_leads_with_the_operators_own_prompt(self):
        card = completion_report.render(self.row())
        self.assertEqual(card.task_id, "t_63cfab7a")
        self.assertEqual(card.run_id, 6)
        first = card.text.splitlines()[0]
        self.assertIn("DONE", first)
        self.assertIn("diwali", first)
        # The generic intake title must not displace the real prompt.
        self.assertNotIn("Telegram: tri-ai", first)

    def test_card_reports_verify_evidence_duration_and_artifacts(self):
        text = completion_report.render(self.row()).text
        self.assertIn("verify: exit 0", text)
        self.assertIn("took 2m 59s", text)
        self.assertIn("model auto/best-free @ custom", text)
        self.assertIn("diwali.html (11.5 KB)", text)

    def test_card_links_artifacts_when_a_dashboard_url_is_known(self):
        text = completion_report.render(self.row(), dashboard_url="http://100.64.0.1:8080/").text
        self.assertIn("http://100.64.0.1:8080/artifact/t_63cfab7a/0", text)

    def test_a_run_that_produced_nothing_says_so_rather_than_implying_output(self):
        text = completion_report.render(self.row(artifacts=())).text
        self.assertIn("produced no files", text)

    def test_a_failed_run_reports_its_error(self):
        text = completion_report.render(self.row(
            outcome="failed", error="verify exit 1", artifacts=(),
        )).text
        self.assertIn("FAILED", text.splitlines()[0])
        self.assertIn("error: verify exit 1", text)

    def test_a_row_without_a_run_id_is_refused(self):
        with self.assertRaises(ValueError):
            completion_report.render(self.row(run_id=None))


class ArtifactReadSurfaceTests(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.workspace = self.tmp / "ws"
        self.workspace.mkdir()
        (self.workspace / "diwali.html").write_text("<h1>hello</h1>", encoding="utf-8")
        self.task = board.create_task(
            self.conn, title="Telegram: tri-ai", prompt="build a diwali page",
            repo=self.workspace, verify_command="true", verify_timeout=30,
        )

    def test_recorded_artifacts_resolve_inside_the_workspace(self):
        board.record_run_artifacts(
            self.conn, task_id=self.task, run_id=1,
            artifacts=[{"path": "diwali.html", "change": "??", "size_bytes": 14}],
        )
        views = terminal.read_task_artifacts(self.db_path, self.task)
        self.assertEqual(len(views), 1)
        self.assertEqual(views[0].path, "diwali.html")
        self.assertEqual(Path(views[0].absolute), (self.workspace / "diwali.html").resolve())

    def test_a_recorded_path_escaping_its_workspace_is_dropped(self):
        board.record_run_artifacts(
            self.conn, task_id=self.task, run_id=1,
            artifacts=[
                {"path": "../escape.txt", "change": "??", "size_bytes": 1},
                {"path": "diwali.html", "change": "??", "size_bytes": 14},
            ],
        )
        views = terminal.read_task_artifacts(self.db_path, self.task)
        self.assertEqual([v.path for v in views], ["diwali.html"])

    def test_only_the_newest_run_is_exposed(self):
        board.record_run_artifacts(
            self.conn, task_id=self.task, run_id=1,
            artifacts=[{"path": "old.txt", "change": "??", "size_bytes": 1}],
        )
        board.record_run_artifacts(
            self.conn, task_id=self.task, run_id=2,
            artifacts=[{"path": "diwali.html", "change": "??", "size_bytes": 14}],
        )
        self.assertEqual(
            [v.path for v in terminal.read_task_artifacts(self.db_path, self.task)],
            ["diwali.html"],
        )


class ArtifactRouteTests(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.workspace = self.tmp / "ws"
        self.workspace.mkdir()
        (self.workspace / "diwali.html").write_text("<h1>Shubh Deepavali</h1>", encoding="utf-8")
        self.task = board.create_task(
            self.conn, title="Telegram: tri-ai", prompt="build a diwali page",
            repo=self.workspace, verify_command="true", verify_timeout=30,
        )
        board.record_run_artifacts(
            self.conn, task_id=self.task, run_id=1,
            artifacts=[{"path": "diwali.html", "change": "??", "size_bytes": 24}],
        )
        self.server = web.create_server(
            host="127.0.0.1", port=0,
            snapshot_fn=lambda: terminal.read_snapshot(
                board_path=self.db_path,
                ledger_path=self.tmp / "ledger.jsonl",
                daemon_state_path=self.tmp / "daemons.json",
                pid_alive=lambda pid: False,
            ),
            event_interval=0.05,
            artifact_fn=lambda task_id: terminal.read_task_artifacts(self.db_path, task_id),
        )
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def test_a_recorded_artifact_is_served_with_its_real_media_type(self):
        with request.urlopen(f"{self.base}/artifact/{self.task}/0", timeout=3) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.headers["Content-Type"], "text/html; charset=utf-8")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("Shubh Deepavali", body)

    def test_an_index_past_the_recorded_set_is_not_found(self):
        with self.assertRaises(error.HTTPError) as caught:
            request.urlopen(f"{self.base}/artifact/{self.task}/7", timeout=3)
        self.assertEqual(caught.exception.code, 404)

    def test_an_unknown_task_serves_nothing(self):
        with self.assertRaises(error.HTTPError) as caught:
            request.urlopen(f"{self.base}/artifact/t_nosuchtask/0", timeout=3)
        self.assertEqual(caught.exception.code, 404)

    def test_the_route_takes_an_index_not_a_path(self):
        # There is no path component to traverse: anything that is not
        # /artifact/<id>/<int> simply is not the artifact route.
        for path in ("/artifact/../../etc/passwd", "/artifact/t_x/../../secret",
                     "/artifact/t_x/0/extra"):
            with self.assertRaises(error.HTTPError) as caught:
                request.urlopen(self.base + path, timeout=3)
            self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
